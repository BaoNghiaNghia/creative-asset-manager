# CAM Etsy Spy — Source-of-truth listing inventory parity (V2)

## Symptom / root cause

Windows Diagnostics counts **eligible distinct Etsy Listing IDs**, retaining both discovery sources. The original CAM projection counted replicated *observation rows*, and the Windows sync exported only one representative record per Listing ID. After multiple Scout runs, inactive older representatives were never retired from CAM. Thus CAM listings and Search/Shop source counts diverged even while agent heartbeats and sync batches were healthy.

Example from the read-only audit on 2026-11-06: Windows API had 68 eligible representative listings (36 Search+Shop, 13 Search only, 19 Shop only). CAM had 98 retained observation rows but 69 distinct Etsy Listing IDs. The Windows TM cache also had 108 product checks and 222 keyword-level entries; CAM incorrectly counted keyword-level pending as **product** TM pending.

## V2 contract

- Source: Windows `/api/spy/matches` (deduplicated eligible Listing ID view), `/api/spy/health` (distinct eligible count); independent SQLite tables remain unmodified.
- Windows worker still sends normal idempotent upsert batches, with `sourceKinds`, `keywordAliases`, source badges and TM evidence.
- Once all deltas are acknowledged, Windows fetches a fresh complete snapshot. It refuses to reconcile if its listing count disagrees with the source health distinct count, duplicate Listing IDs exist, or the snapshot exceeds protocol limits.
- The worker sends the **complete current representative identity set** to `POST /api/v1/etsy-spy/agents/{agent_id}/listing-inventory`. Request includes `schema_version=1`, ISO 8601 `snapshot_at`, and `listings` with `sessionId`, `keyword`, `sourceKind`, `listingKey` only. The request is authenticated using the existing agent bearer token and is never sent to browsers.
- CAM verifies the entire identity set already exists (HTTP 409 and **no changes** for incomplete/unacknowledged batches), rejects duplicates and stale snapshots, and atomically flips `is_current` on that agent's listings. **No historical listing row is deleted.** The source flags `has_search` and `has_shop` are recovered from the representative's `metadata_json.sourceKinds`.
- The read API, CSV export and summary consider **current** listings only. Total uses distinct Etsy Listing IDs. Source counters use independent distinct counts over `has_search` and `has_shop`; they overlap where Windows did. Product TM pending means an active eligible listing without completed product TM evidence. Pending keyword-level trademark checks stay accessible in the Trademark tab, but do not inflate the product pending number.
- `last_inventory_at` and `last_inventory_count` expose synchronization progress. Windows records the successful inventory digest and refreshes every 5 minutes, or immediately if the source listing identity set changes. After an outage, the last confirmed CAM projection remains visible; no empty snapshot is silently inferred.
- Migration: `0196_etsy_spy_inventory` adds three listing booleans and last-inventory metadata to the existing agent table. All previous rows remain readable until the first verified source reconciliation.

## Verification

```sh
# Linux CAM server, isolated migrations / regression
PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest -q apps/api/tests/modules/etsy_spy
npm --prefix apps/client test -- --run app/etsy-spy/EtsySpyPage.test.tsx
npm --prefix apps/client run build
```

```bat
REM Windows tool on D:\CAM Etsy Spy Product
set CAM_TEST_SYNC_LIVE=1&& node --test scripts\test-cam-sync.mjs
node scripts\cam-sync-status.mjs --snapshot
node scripts\check-spy-db-live.mjs
```

Expected parity after successful inventory reconciliation:
- CAM Eligible listings = Windows `stats.matches`
- CAM Search = Windows `stats.search`
- CAM Shop = Windows `stats.shop`
- CAM keyword nodes = Windows `stats.keywords`, subject to up to 60s sync latency
- Product TM labels use the Windows *product title* TM cache; “no match” is not legal clearance
- CAM `last_inventory_at` updated and Windows `cam_inventory_count` equals its representative listing count

## Safe rollback

Source SQLite and Chrome/Scout are never restarted for a CAM-only deployment. Keep current + previous immutable backend/frontend releases. For CAM code rollback, older code will ignore new inventory columns; do not downgrade a populated production schema without explicit review. The Windows worker may keep old confirmed CAM data visible while the new endpoint is unavailable; the retry queue retains source snapshots and eventually resumes.
