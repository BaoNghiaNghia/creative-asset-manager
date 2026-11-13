# CAM Etsy Spy — Agent review receipt protocol (2026-11-13)

## Contract

The Windows Etsy Spy Chrome extension DOES NOT receive a CAM secret and cannot
write user selections directly. It may only create a verified request in its
localhost SQLite `spy_review_intents` inbox, source-owned by an eligible Etsy
listing and initiated by a real user on the current Search tab.

The paired Windows CAM sync agent sends at most three pending requests per
cycle over HTTPS using its existing agent Bearer token:

`POST /api/v1/etsy-spy/agents/{agent_id}/review-intents`

JSON: `{"requestId":"review-...","listingKey":"123456","action":"pick","keyword":"Vintage Shirt","evidenceHash":"32-hex"}`.
Actions: `pick`, `later`, `win_keyword`.

Success returns `{"ok":true,"requestId":"...","listingKey":"...","action":"pick","keyword":"...","status":"cam_confirmed","outcome":"created|already","confirmedAt":"..."}`.

`GET /api/v1/etsy-spy/agents/{agent_id}/review-intents/{request_id}`
returns the same receipt, authenticated by the same agent. No receipt for a
foreign agent/tenant is returned (404).

## Authorization and verification

- CAM authenticates an active paired **Windows Etsy Spy agent**. Image-agent
  identities are explicitly forbidden. No browser cookie, CSRF bypass or
  frontend permission elevation is involved.
- The CAM backend independently rechecks a **current** source listing
  synchronized by the **same agent** under the same tenant/listing ID and
  keyword. A forged Etsy Search card cannot become a CAM Pick.
- Existing client qualification is preserved: own source badge plus actual
  demand, **or** first-20 Shop listing with recorded `In 20+ carts`
  lower-bound evidence, listing-specific `itemReviews > 3`, and an Etsy shop
  provenance URL. Blacklist, Custom title, auto-Later exclusion, Pick/Later
  conflict and source limits still apply.
- The request ID, action, listing, keyword and evidence fingerprint are
  SHA-256 bound in a durable `etsy_spy_review_receipts` row. Replays with the
  same payload return the original receipt. Reuse of an ID with another
  payload returns HTTP 409. The actual Pick/Later/Win effect and receipt row
  commit in **one PostgreSQL transaction**.
- Only after validating the exact authenticated CAM response does Windows
  mark its local intent `cam_confirmed`. No HTTP success without a matching
  receipt is accepted. Unknown 404, network loss, 5xx, 429, CAM evidence
  still catching up and 401/403 do not mark confirmed.
- Existing CAM Pick creates the normal extraction/TikTok jobs and SMS picks
  feed; Later seeds recalculate the established topic matching; Win Keyword
  stays a passive tenant-owned note.

## Data and migration

Alembic `0208_etsy_spy_agent_review_receipts` (after `0207`) adds:

- `etsy_spy_review_receipts`: tenant + agent + request primary key and
  matching payload digest / outcome / confirmed timestamp.
- Nullable `etsy_spy_listings.cart_lower_bound` and `item_reviews`, populated
  from the authoritative Windows source. Prior rows remain valid and the
  absence of Shop evidence is *not* a positive eligibility signal.

The Windows sync projection adds `reviewEvidenceSyncV1` only to strict,
previously eligible Shop records so historical qualifying listings re-sync
once without recrawling Etsy or forcing all listing history to resend.

The downgrade is blocked when receipt or Shop evidence data exists. Standard
rollback is **code-only** with the additive tables/columns retained.

## Safe activation order

1. Review migration and release backend from clean main via the standard
   guarded CAM deployment scripts. Preserve current + previous production
   releases. Validate live version/readiness and schema.
2. Confirm Windows sync agent is correctly paired and its last source
   snapshots have reached CAM. Deploy Windows source separately.
3. Wait for an authoritative **paused treeNext / zero lease** Scout boundary
   before coordinated Chrome extension reload. The local review inbox API
   cannot activate by merely editing source files.
4. Once both APIs and installed extension have been verified live, create the
   Windows **`data/review-sync-enabled.flag`** in the approved rollout step
   and restart ONLY the independent outbound sync worker if needed (never
   the active Chrome tab). Existing pending requests then process in order.
5. Observe `review_cam_confirmed`, `review_cam_rejected` and the actual CAM
   Picked/Later/Win screen. Do not declare "already Picked" from a local
   queued state.

## Test and QA

- CAM: `PYTHONPATH=apps/api python -m pytest
  apps/api/tests/modules/etsy_spy/test_etsy_spy.py -q` in an isolated
  dependency environment; include tenant/pairing, idempotent replay, Shop
  source-owned qualification, and conflict tests.
- Windows: `node scripts/test-review-sync.mjs`, `npm run test:phase8`,
  `npm run test:queue-v3`, `npm run test:load-recovery`.
- No new paid provider API, new Chrome tab, local-to-cloud tunnel, or
  production credential in source control.
