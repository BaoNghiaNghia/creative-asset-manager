# BD-SCAN-CALLBACKS-02B.2b — Bounded storefront scope preview (no execution)

Implemented 2026-10-18 on the Branding DNA feature branch. The server and
future-worker independently validate whether a candidate URL belongs to the
**same registered storefront subtree**. This is a conservative structural
check, *not* proof of Amazon/Etsy automation permission and *not* a signed
executable task.

## Server contract

`POST /api/v1/branding-dna/shops/{shop_id}/scan-scope/preview`

Requires tenant-bound `ai_provider.configure`, JSON:

```json
{"task_id":"<existing staged task UUID>","candidate_url":"https://www.etsy.com/shop/Hats/section"}
```

The API resolves the task, associated snapshot and registered shop from the
authenticated tenant only, and refuses cancelled/non-staged jobs. A cross-
tenant shop/task always returns 404. The candidate URL is never fetched,
resolved by DNS, downloaded, logged to evidence, or returned as source output.

Response is a non-executable preview: `candidate_within_storefront`,
`scope_reason`, `shop_access_declared`, static budget, blockers, plus
`job:null`, `dispatchable:false`, `scan_enabled:false`,
`marketplace_authorization_verified:false` and `Cache-Control:no-store`.
A positive scope match *only* indicates path structure; it cannot transition
tasks or grant source/media rights.

## Scope and bounds

- Allowed HTTPS hosts: Etsy `www.etsy.com`; Amazon explicit US/UK/CA/DE/FR/
  ES/IT/JP marketplace host allowlist. No arbitrary suffix or third party
  host, no IP/localhost, alternate port, userinfo, Unicode confusables.
- Etsy: exact registered `/shop/{shop}` path. Amazon: exact registered
  `/stores/{shop}/...` path. Up to **two** descendant path components beyond
  that registered root; no changing marketplace or sibling storefronts.
- Raw URL rejects percent escaping, dot traversal, doubled path separators,
  backslashes, embedded credentials, query/fragment, control characters and
  path depths beyond the approved bound. Product pages at `/dp/` or
  `/listing/` outside the registered storefront are rejected even if they
  belong to the same business; require a separately approved future adapter.
- Display-only pilot budget (not an execution grant): **5 pages**, at most
  **20 gallery items**, **20 seconds/navigation**, **zero redirects**, two
  extra path segments, `request_allowed:false`.
- `apps/branding-dna-worker/src/scan_scope_guard.mjs` mirrors validation as
  defense in depth, but is **not imported by the live agent** or browser pilot.
  The live worker's `/lease` remains `job:null,scan_enabled:false`.

## UI

On **Branding DNA → Browser Scanner → Điều kiện quét theo shop**,
administrators can select a staged task and manually enter a URL for a
read-only scope preview. Viewers with no `ai_provider.configure` policy
visibility do not see this panel. Positive and negative results still show
"Trạng thái thực thi: Đã khóa" and the mandatory marketplace permission gate.

## Testing and release gates

```bash
cd apps/api
.venv/bin/python -m pytest tests/modules/branding_dna/test_scan_scope.py -q
cd ../branding-dna-worker
npm test
cd ../client
npm run typecheck
npm test -- --run app/branding-dna/BrandingDnaScopePreview.test.tsx
```

Tests cover strict URLs, path segment boundaries, encoded/decoded traversal,
host/port/credential/redirect tricks, mixed tenant/shop/snapshot tasks,
cancelled jobs, no mutation, zero I/O and the worker's independent validator.

**Still missing**: external provider-authorized access per shop, signed server
capability with short TTL, agent-shop binding, policy/robots enforcement at
execution time, per-tenant kill switch, source-result idempotency/outbox,
media rights, Windows real-browser QA, and CI/recovery gates. Passing scope
preflight must never be used to claim any of these. No new DB migration,
secrets or external dependencies in this feature. Code-only rollback.
