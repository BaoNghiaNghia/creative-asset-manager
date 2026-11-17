# CAM Etsy Spy private derivative CDN (P2)

**Disabled by default in production.** This directory contains a stand-alone,
fail-closed Worker for serving *only* immutable saved Listing Image thumbnails
(PNG, 192 px) and review previews (WebP, 720 px). It never serves/stages
original Listing Image PNG or native Design Master PNG. Google Drive remains the
canonical, private source of truth. This is a staged deployment artifact:
Cloudflare credentials, bucket, and CSP must be configured explicitly.

## Security model

1. Browser GET goes through `/api/v1/etsy-spy/listing-images/{pack}/{slot}/{quality}`
   with the normal CAM cookie/principal, active tenant and Picked ACL.
2. After authentication, CAM issues an HMAC ticket good for **45 seconds**.
   The Worker rejects expired or tampered URLs, unknown keys, and unsigned requests.
   Signatures use `HMAC-SHA256(method + "\\n" + path + "\\n" + expiry)`.
3. Object key is `v1/{HMAC(tenant)[:24]}/{pack}/{slot}/{quality}/{originalSha}.{ext}`
   (preview includes `-w720v1`). A different tenant cannot guess its key or
   reuse a ticket for another key.
4. The Worker bucket is private, no public R2 domain. The Worker never exposes
   list operations. Uploads require an independent signed PUT and verified payload
   checksum; body size is capped at 2 MiB.
5. Requests without valid signatures fail closed. The Worker returns
   `Cache-Control: private`; no `Cache Everything` or public edge caching.
   Revoking Picked permissions stops new CAM tickets; previously issued URLs can
   still work for up to 45 seconds. Use this only for low-risk UI derivatives.
6. Worker unavailable / missing object / configuration absent => CAM falls back
   to authenticated Google Drive source automatically. Do **not** fall back
   to public buckets.
7. The browser stores authenticated bytes but revalidates through CAM on
   every new request (ETag/304 and `private, no-cache`). A removed Picked
   permission cannot be bypassed by an unexpired browser `max-age`.
8. CAM caches R2 existence for 5 minutes when present or 20 seconds when
   missing (5 seconds on Worker errors); a successful upload promotes the
   object immediately. At most two uploads per API process are in flight.
   Only aggregate, PII-free HIT/MISS and stage latency metrics are emitted.

## Preparation

1. Confirm the Cloudflare account, its Worker quota and R2 pricing before use.
2. Install Wrangler in a separate deployment environment. Create the
   `cam-etsy-image-derivatives` **private** R2 bucket.
3. Generate a new independent 32-byte secret and set it both as Worker secret
   `CAM_HMAC_HEX` and CAM environment `CAM_ETSY_IMAGE_CDN_HMAC_HEX`.
   Never commit or log its actual value.
4. From this directory run `npx wrangler secret put CAM_HMAC_HEX` and
   `npx wrangler deploy`. Record the HTTPS Worker URL.
5. Whitelist **only** that exact HTTPS Worker origin in Nginx CSP `img-src`
   before enabling CDN redirects. Nginx CSP is not altered by this patch.
6. Set CAM environment variables:
   - `CAM_ETSY_IMAGE_CDN_ENABLED=true`
   - `CAM_ETSY_IMAGE_CDN_WORKER_URL=https://<worker>.<account>.workers.dev`
   - `CAM_ETSY_IMAGE_CDN_HMAC_HEX=<64 lower-case hex characters>`
   - `CAM_ETSY_IMAGE_CDN_CANARY_TENANTS=boostgame` (required allowlist;
     no tenant is redirected if the key is absent)
7. Before enabling, run the **read-only** preflight on the CAM host:

   ```sh
   python3 deploy/cloudflare/etsy-image-cdn-worker/preflight.py --probe
   ```

   It validates the allowlist and secret format without printing secrets,
   checks CSP and sends a signed **synthetic** HEAD to the Worker. It must
   report `ready_to_enable=true`; 404 on that nonexistent synthetic object
   proves the deployed signing key and private bucket are connected.
   Do not enable while this check is blocked.
8. Reload the backend and test one Picked listing with a saved thumbnail.
   First miss fetches Drive and schedules best-effort Worker PUT; subsequent GET
   may redirect to Worker if signed HEAD confirms the R2 object exists.
   Never turn this on before CSP and tenant-isolation checks pass.
9. Start with a single tenant/canary and monitor 302, 404, 403, 5xx, latency,
   cache HIT and misses. Roll back by setting
   `CAM_ETSY_IMAGE_CDN_ENABLED=false` and restarting backend. Existing
   Google Drive images remain unaffected.

### Tests

```sh
node --test index.test.mjs
```

P0/P1 work without any Cloudflare account or credentials. P2 is an optional
read-through/write-through accelerator; if Cloudflare is not provisioned,
leave it off. Do not copy an unrelated video bucket/secret.
