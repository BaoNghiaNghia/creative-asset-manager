# CAM Etsy Spy — Picked → Windows image backfill (V1)

Date: 2026-11-09

## Objective

The Windows Agent (`D:\CAM Etsy Spy Product` v1.24.26) already supports
a complete, revisioned Picked listing-ID feed. This release provides the
missing CAM endpoint without changing the existing Pick/Unpick, trademark,
qualification, or extraction-job workflows.

## GET agent endpoint

`GET /api/v1/etsy-spy/agents/{agent_id}/picked-listings`

Authenticate with the existing paired Windows Agent bearer token. No browser
login or cross-tenant access is allowed. Return:

```json
{
  "ok": true,
  "complete": true,
  "total": 1,
  "revision": "64 lowercase hex characters",
  "items": [
    {"listingId": "1234567890", "pickedAt": "2026-11-09T00:00:00.000000Z"}
  ]
}
```

The snapshot contains all Picked Etsy listing IDs for the authenticated
agent's tenant, independent of their current discovery status. IDs are sorted.
`revision` is SHA-256 of a canonical serialization of the full membership
and Picked timestamps. Repeated reads without changes have the same checksum.
Pick/Unpick changes the checksum. A valid empty set is `complete:true`,
`total:0`, `items:[]` and a deterministic checksum.

The API uses a single bounded SQL query, rejects invalid IDs/timestamps,
returns HTTP 413 instead of truncating more than 10,000 rows, and sends
`Cache-Control: no-store`. A transient error must not be interpreted as
an empty selection.

## Windows consumer

The Windows outbound CAM Sync worker queries the endpoint once every five
minutes. It validates the complete snapshot, total and checksum shape. It
mirrors only confirmed snapshots to its existing loopback-only
`POST /api/spy/picked-listings` endpoint and local SQLite
`spy_picked_listings`. A 404, timeout, malformed/partial response or
token rejection preserves all previous local picks.

The image-URL backfill queue prefers Picked listing IDs ahead of other
eligible items; within each class it honors retry counts, demand scores and
age. This does **not** download images, trigger design extraction, introduce
additional Etsy tabs, or modify the original Spy eligibility rules.

## Verification

- `PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest -q apps/api/tests/modules/etsy_spy`
- Windows: `node scripts/audit-cam-picked-read-contract.mjs` should
  report `/picked-listings` HTTP 200 with `complete`, `total`,
  `revision` and `items`.
- Local `GET /api/spy/picked-listings` should show a non-null revision
  after the next authorized five-minute pull.
- `GET /api/spy/image-revisit/status` exposes `pickedPending`.
- Verify Windows and CAM inventory parity and Queue V3 lease remain stable.

## Rollback

Rollback only CAM API code if necessary. The previous release returns 404
for the new agent endpoint; Windows retains its last verified Picked cache.
Never clear the Windows SQLite database or reset the Chrome Spy checkpoint.
