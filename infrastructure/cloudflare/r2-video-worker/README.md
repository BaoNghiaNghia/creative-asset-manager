# Private R2 original-video delivery Worker — Phase 3A/3B with Phase 4 rollout

This Worker accepts a short-lived read ticket and streams the original object
from a private R2 binding. It does not authorize CAM users or shares; Phase 4B
performs application authorization before issuing a ticket. Public Review uses
the Worker only when the persisted runtime delivery gate is effective.

## Ticket contract

The only accepted key is `video-cache/{tenant_id}/{lowercase_sha256}/original`
(no extension or filename); the pathname is exactly `/{key}`. The tenant ID
matches `[A-Za-z0-9][A-Za-z0-9_-]{0,254}`; SHA-256 is 64 lowercase hex
characters. Percent encoding, traversal, backslashes, controls, duplicate
separators, foreign prefixes and alternate suffixes are rejected.

The exact UTF-8 HMAC-SHA256 message has **no trailing newline**:

```text
v1
read
/video-cache/{tenant_id}/{lowercase_sha256}/original
{exp}
```

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
2. Review the actual `VIDEO_CACHE_BUCKET` binding and Worker route/domain.
3. Set the same high-entropy, at-least-32-byte secret on backend and Worker
   through protected configuration. The Worker command is
   `wrangler secret put R2_VIDEO_MEDIA_SIGNING_SECRET`; do not commit it.
4. Set the backend media base URL to the approved HTTPS Worker origin and
   align its 1–3600-second TTL with the Worker maximum.
5. Review and test in staging; authorize any Worker deployment, DNS, secret
   or bucket-setting change separately.

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
bucket remains private; no r2.dev, Worker route, custom domain, DNS, secret, or
deployment was configured. A future approved route would be media.<domain>/*
with the private VIDEO_CACHE_BUCKET binding and a Worker secret.


## Phase 4C production preflight contract

The repository CI runs `npm test` and `npm run typecheck` for this Worker on
every pull request and push to main.

For an approved production rollout, keep the application runtime delivery
toggle OFF while configuring the Worker. The API-side Phase 4C preflight can
optionally send one signed HEAD request for a random, absent preflight key. The
expected result is 404: this proves the route accepted the HMAC ticket and then
looked up the private R2 binding. The probe performs no PUT/DELETE/list and does
not use a user asset. A 403 or other response fails rollout readiness.


## Phase 4D production configuration and canary scope

Use `deploy.tools.r2_video_worker_rollout` from the repository root to render
`wrangler.production.json`. The generated file is ignored by Git and contains
no secret value. It uses a Custom Domain, keeps `workers_dev=false`, binds the
private R2 bucket as `VIDEO_CACHE_BUCKET`, and declares the signing secret as
required.

The rollout helper's `plan` subcommand does not execute Wrangler. It labels
which suggested commands mutate Cloudflare and keeps the application runtime
gate OFF as a rollout invariant.

Application traffic is additionally scoped by
`VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS`. Empty canary scope with global rollout
false is deny-all. Promotion to all tenants requires the separate
`VIDEO_CDN_DELIVERY_GLOBAL_ROLLOUT_ENABLED=true` setting and an empty canary
list.

For the production canary, use the application Phase 4D preflight with both
`--probe-worker` and `--probe-ready-object` before enabling the persisted
runtime gate.
