# CAM Etsy Spy read projection — integration v1 (2026-11-06)

## Responsibilities and security

- CAM reads a **tenant-scoped projection** of Windows Etsy Spy data at
  `/etsy-spy`. Authorized users receive independent **etsy_spy.read**;
  **etsy_spy.manage** is required for pairing, revocation and rotation.
  Member-specific navbar grants apply server-side, too.
- The Windows Chrome extension, Node service on **127.0.0.1:4327**, SQLite
  `cam-etsy-spy.sqlite` and `trademark.sqlite` remain independent sources.
  Neither the Windows HTTP port nor either SQLite database is published.
- Data enters CAM only through a scoped 64-character machine bearer secret,
  hashed at rest; tokens are shown only once on administrative pairing/rotation.
  Read-only principals cannot pair. Invalid/revoked tokens cannot push.
- CAM stores seven additive, indexed tables introduced in Alembic
  `0195_etsy_spy_projection`: agents, batches, sessions, keywords,
  listings, trademark checks and system logs. All include tenant_id.
- Synchronization accepts up to **200 records and 2 MiB per batch**.
  Durable unique batch ID and exact SHA-256 hash prevent duplicate imports
  and reject conflicting replays. The Windows outbox is committed before push,
  and local record fingerprints update only after server acknowledgement.
  No snapshot deletes an earlier CAM record just because it disappeared.
- CSV exports use the currently selected server filters and escape spreadsheet
  formula prefixes. Timestamp display uses Vietnam/GMT+7; UTC in storage.
- Etsy demand labels are not recomputed, null demand numbers stay null.
  `Custom` titles are excluded. Product TM matches are limited to phrases
  present in actual product titles, not just query keywords.
  LIVE signals possible conflict, while DEAD/no-match is not clearance.
  Source as-of date and checked date remain visible; source verification
  belongs to USPTO TSDR.

## Operator setup

1. Verify the CAM backend release has migrated PostgreSQL to
   `0195_etsy_spy_projection`; verify `/live`, `/ready` and `/version`.
   Backend and frontend deployments come **only from main** and preserve the
   current release plus one independent rollback.
2. Admin opens CAM `/etsy-spy`, clicks **Pair Windows agent**, copies the
   one-time Agent ID and Token. Never put these into Git or browser storage.
3. On `DESKTOP-3FR0LF0`, open `D:\\CAM Etsy Spy Product`, run
   `CONFIGURE_CAM_SYNC.cmd`; paste the credentials when prompted. The config
   is stored locally at `data\\cam-sync-config.json`.
4. Restart the independent Node Supervisor using its normal launcher
   if needed. It starts `scripts/cam-sync-agent.mjs` as a *child worker*,
   without restarting Chrome or replacing the source SQLite databases.
5. Inspect `data\\logs\\cam-sync-agent.jsonl` locally, and confirm
   the CAM dashboard shows a fresh heartbeat and increasing counts.
   Reconciliation should measure real source counts, not handoff examples.
6. Test disconnection: stop only the sync worker, not the source Spy.
   The CAM dashboard continues to show the last data and Offline state.
   Restart sync, verify outbox replay without added duplicates.

No remote browser control, auto TMHunt request, new Windows proxy,
VPN/tunnel, or external API charge is introduced by this phase.
TM lookup on CAM is read-only against synchronized cached evidence.

## Verification

- Backend: `pytest apps/api/tests/modules/etsy_spy` verifies auth,
  idempotency, title-only matching, tenant isolation, replay, offline
  retention, CSV escaping and Alembic upgrade/downgrade in a shadow DB.
- Frontend: `vitest run app/etsy-spy/EtsySpyPage.test.tsx` plus
  `AppRoute.test.ts` and `WorkspaceNavigation.permissions.test.tsx`.
- Windows: `node --test scripts/test-cam-sync.mjs`; optional live read-only
  source snapshot: `set CAM_TEST_SYNC_LIVE=1&& node --test scripts/test-cam-sync.mjs`.
- Before production rollout, run the standard CAM API tests, build and
  official immutable release scripts. Never force a downgrade that drops
  nonempty `etsy_spy_*` tables.

## Version/rollout constraints

The Windows source may become unavailable without losing CAM records.
The current Windows source remains authoritative for qualification, browser
checkpoints, raw observations and trademarks. A future phase may add an
audited command queue with separate authorization for TM rechecks.
