# CAM Image Orchestrator V1.2 - implementation decisions and gates

2026-11-25. Source baseline d10bc1c8abfc155358041ff08899001d69b147d1.
Status: P0 domain policy implemented and offline-tested; NOT deployed, NOT connected
to production workers. No new database or service; existing PostgreSQL remains authority.

## Final compatibility decisions

- Image endpoints retain the Muse contract V1.2 compatibility prefix
  /api/v1/muse-bridge but are implemented in an independent image_orchestrator
  router/service, with separate database tables, queues, command types, leases,
  settings, RUN/STOP, retry, cooldown and receipts. Video paths, video schema,
  cron and runner body are unchanged.
- Register image capability in the existing worker identity authority, not a
  second credential roster. Require server-owned image capability proof,
  exact tenant and token-derived worker identity, no claim by default.
  ChatGPT extension uses its paired Image Agent auth adapter, never a Muse
  video credential. Existing identities remain scoped to their existing rights.
- Resource Manager must grant a slot atomically in the existing DB, with
  fenced owner/epoch and explicit renderer/quota groups. A read-only busy
  check is not a lock. For a shared resource with an unintegrated video runner
  the image capability stays disabled until actual mutual exclusion is proven.
  Keep video independent; no mandatory dependency on Listing Image role 01.
- Corrected Skill candidate: 2026-11-25.skill16-tagfix, ZIP SHA and per-file
  SHA pinned in skill_registry_candidate.json. The predecessor is retained as
  historical; new pack admission requires the corrected hash. This manifest
  is NOT a served registry or proof of installation. Palette and full
  placement plan must be verified before any production claim.
- Deterministic 64-character pack ID is SHA256 of canonical
  [protocol, tenant, listing, full artwork SHA, garment SKU, revision].
  Compatible with existing EtsySpyListingImage.pack_id VARCHAR(64).
  Persist source and placement lock SHA plus immutable role/skill/palette plan.
  Do not silently reuse the identity with a changed lock/plan. Legacy packs
  and completed slots retain original identity.
- Wave 1 roles 01-04; wave 2 05-13. Frozen membership per batch, 24h soft
  deadline. Atomically persist the first release decision; isolate terminal
  or timed-out Wave 1 packs into recovery, not forced cancellation. A
  healthy in-flight renderer can still finish its owned role. The isolation
  decision must be monotonic; later recovered packs enter a separate batch.
- Lease 3600s, heartbeat 60s, separate command and image tokens, common
  atomic expiry; persistent pack owner stays across waves. STOP denies new
  claims and role starts, permits owner heartbeat/receipt/complete for already
  started work. Token digests only in database; redact logs, never return
  raw tokens in status or receipts.
- Before renderer invocation commit role-start with attemptId, wave, owner
  and epoch. If ACK lost, reconcile; no second Send. Timeout or expiry
  marks reconcile_required, not confirmed failure. Retry only with verified
  no usable result (max one first attempt + two retries). Do not authorize
  a retry based on untrusted worker flags. Manual pack owner release only
  after verified resolution, no active lease; prefer original owner.
- Storage and QA are separate evidence: native square PNG, actual bytes SHA,
  matching artwork/placement/role/SKU and QA image SHA, server-verified Drive
  result. 1600px allowed as pilot/internal preview, never a final publication
  pass solely due to verified storage. Existing >=2000px publication gate
  retained. Never pad, crop, stretch or upscale to bypass. Role 13 requires
  verified QA-pass 01/05/06/07 source receipts; preview can support internal
  pilot only. Do not assert physically measured 150mm without calibration.
- Partial/failed completion derived from saved roles; no completion while
  dispatch is ambiguous. A matching ACK replay within 24h cannot mutate a
  later lease. Status/receipt must identify tenant/listing/pack/SKU/revision.
- Original SHA changed = new revision only. No silent deletion/re-generation
  of previous receipts. Hold source unavailable for affected revision in
  both flows; image-only QA hold never blocks video. Only explicit listing
  pause may affect both.
- Legacy Extension cutover: read-only inventory of local ledgers and
  published receipts; drain or quarantine in-flight Skill roles; no double
  claiming between legacy and central schedulers. Inventory/Target untouched.
  After receipt migration, switch only Skill admission at a safe boundary.

## Implementation phases

P0 delivered in source: pure policy.py, versioned registry candidate, unit
tests. No migrations, no live router mount, no production changes. Pure
policy is NOT concurrency control; the next phase must persist transitions
in a tenant-scoped transaction and use PostgreSQL locks/unique constraints.
No new dependencies for P0. Rollback: omit/remove the new unreferenced module
and tests; no migration or data rollback needed.

P1 required before any live claim:
- Add additive migration and repository: image batch, pack, role/attempt,
  wave command, lease/ACK, capability state, resource reservations.
  Lock pack and batch rows for all transitions; atomically enforce one owner,
  renderer capacity and immutable per-role receipt. No separate DB.
- Integrate existing shared artwork, SKU/palette, Drive adapters and auth.
  Preserve existing ListingImage immutable slot and deletion tombstone
  semantics; do not impersonate an Extension agent to upload Muse PNGs.
- Make batch release persistent and monotonic. Resolve preflight conflict
  between legacy local ledger and central pack. Verify palette and actual
  placement evidence, exact corrected skill installed on both workers.

P2 image-only API (compatibility paths, no video changes):
GET /image-commands, /image-work, /skill-registry
POST /image-commands/{id}/claim, /heartbeat, /complete, /yield, /skip-report
POST /jobs/{id}/images/claim
GET /jobs/{id}/image-contract?packId=...&sku=...
GET /jobs/{id}/image-status?packId=...&sku=...
POST /jobs/{id}/images/{role}/start (packId, sku, epoch, attemptId; persisted before send)
POST /jobs/{id}/images/{role} (raw PNG; authorized attempt and QA evidence)
POST /image-workers/cooldown (image only, separately scoped)
Role-start response must distinguish first grant from replay. For image
upload add role attempt ID, epoch, source/placement/skill SHA and a bounded
QA record bound to actual PNG SHA. Exact upload/claim paths are served by CAM;
never follow worker-supplied arbitrary URLs. Do not use video /workers/cooldown
for image scope until backward-compatible shared service is verified.

P3-P7: extension adapter, Muse adapter, isolated RUN/STOP dashboard, real
5-listing pilot (65 roles, global barrier), bulk only after >=4/5 complete
with zero duplicates/drift/lease losses and real resume + cron capability
proof. 1600px pilot completion and publication eligibility are separate
metrics. No cron activation, account credential changes or production job
dispatch before explicit readiness checks.

## Tests and next gate

P0 stdlib unit tests: 12/12 passed on CAM source host. Test data only, no real
provider or storage calls. Muse offline preflight is a separate handoff, not
installed on the Muse VM. Database concurrency/migration, live API, shared
resource arbitration, real renderer, Drive upload, Extension migration,
video regression and pilot are NOT_RUN. Keep image claims disabled.

## P1 persistence checkpoint (2026-11-25)

Muse reported the P0 offline handoff passed 7/7; installed corrected bundle
19/19 hashes match and runner remains PREPARED_DISABLED. Native 1600 square
is eligible for pilot/preview only. Keep publication >=2000 until an explicit
renderer-scoped quality policy is approved; do not upscale.

Source changes: image_orchestrator/model.py and repository.py, shared
render_resources/model.py and service.py, additive migration
0217_cam_image_orchestrator, metadata imports in app/main.py and Alembic env,
test_repository.py. No existing video path, schema, scheduler or body changed.
The migration creates exactly five tables. Downgrade refuses to drop any
nonempty new table. State persistence uses tenant-scoped pack/role keys,
database unique constraints and explicit SELECT FOR UPDATE; batch release
is persisted once, late pack cannot re-close it. STOP/control row defaults
false. Resource integration flag defaults false; expired resource ownership
requires reconciliation, never automatic steal. No new dependencies.

Verification: 22/22 passed (12 policy + 10 persistence/migration/resource
tests) using copies of the exact source in ops/image-orchestrator-p1-offline-test
with the installed production Python dependencies but isolated in-memory
SQLite and a standalone declarative Base. No CAM runtime, DB credentials,
network, actual Drive, renderer or production DB access. SQLite tests do NOT
prove PostgreSQL concurrency. PostgreSQL row-lock contention and release
migration tests against an isolated PostgreSQL environment remain required.
Live baseline advanced from d10bc1c8 to f22e235d during work; reconcile with
latest production source before release. No production migration, router,
claim capability or cron enabled.

Remaining P1/P2 work: verified shared resource integration with both actual
runners; capability and quota adapter, authoritative artwork/plan/palette
admission, recovery batch requeue for isolated packs and proven late receipt
reconciliation, Drive/Gallery adapter (respect immutable receipts and
deletion tombstones), image-only commands and API auth, real PostgreSQL
concurrency and full migration chain verification, legacy Extension drain.
The existing repository transition accepts internal trusted callbacks only;
never expose a caller-supplied resource/flow/QA boolean as proof.

Rollback: before deploying, omit candidate changes; after future deployment,
disable image admission and preserve additive tables and receipts. Only
empty-table downgrade permitted. No change to existing video deployment.

## P2a authenticated read API checkpoint (2026-11-25)

Source-only candidate. New identity.py and read_api.py in image_orchestrator;
mounted in app/main.py independently of video router, disabled by default
via CAM_IMAGE_READ_API_ENABLED (read endpoints only, not an execution flag).
No production environment changes or migration applied.

- GET /api/v1/muse-bridge/skill-registry: candidate manifest with pinned
  corrected ZIP and 19 member hashes; explicit PREPARED_DISABLED,
  paletteVerified=false, holdIsolationSeconds=86400, leaseSeconds=3600,
  heartbeatSeconds=60. No assertion of live renderer readiness.
- GET /api/v1/muse-bridge/image-commands?limit=20: always empty with
  reason IMAGE_CLAIMS_DISABLED and queueAuthoritative=false. Never infer
  QUEUE_EMPTY or absence of legacy work from this response.
- GET /api/v1/muse-bridge/image-work?limit=20: tenant-scoped central pack
  and role counts. remainingWithoutImages=null until ledger cutover.
  flowRunning=false regardless of requested control state.
- GET /api/v1/muse-bridge/jobs/{listing_id}/image-status?packId=<64-hex>&sku=C1717:
  owned pack only, exact tenant/listing/pack/SKU scope; allowlisted receipt,
  no raw lease or lease hashes, corrupt verified receipt projects
  reconcile_required. Historical storage and QA receipt only, not fresh
  Drive verification or proof of the latest artwork. Contract ready=false.

Authentication: reuse existing Muse bearer resolver, deny legacy shared
identity; existing paired Extension Image Agent resolver selected by
X-CAM-Image-Agent-Id canonical UUID, image_only=true. No fallback between
auth types. Extension's stable derived identity is chatgpt-ext-<agent UUID>
rather than a caller-supplied alias. All responses are private, no-store.
No image mutation routes implemented yet. No new credential roster.
Video endpoints and video scheduler unchanged; no video integration test
or live Muse authentication performed in this isolated test.

Tests: 32/32 passed (12 P0, 10 P1, 10 P2a); isolated TestClient and SQLite,
existing auth adapters mocked to test identity routing and denial behavior.
No production DB or renderer calls. Actual PostgreSQL contention, full
release migration, live auth and full video regression still required.
P2b must implement real command lifecycle, atomic resource/flow/pack
admission, role-start and late receipt reconciliation, shared Drive/Gallery
storage with actual PNG validation, source/plan/palette registry validation.
Only then can the Muse runner do a live preflight. Keep PREPARED_DISABLED.

## P2b command lifecycle and Drive adapter checkpoint (2026-11-25)

Source candidate only, not production-ready. Add migration 0218_cam_image_commands:
three image-only tables cam_image_commands, cam_image_capability_states,
cam_image_upload_intents plus approved_plan on cam_image_packs. Both 0217 and
0218 are additive; downgrades refuse non-empty new state. No separate database.
Image capability settings reference existing authenticated identities, not a
new credential roster. Stable UUID commandId per full pack ID and wave.
An immutable approved plan must match plan_sha256; source/palette/legacy
drain approval must be provided by a trusted adapter.

Implemented HTTP candidate routes under /api/v1/muse-bridge:
- POST /image-commands/{commandId}/claim (empty body)
- POST /jobs/{listingId}/images/claim?commandId=... (lease, epoch)
- POST /image-commands/{commandId}/heartbeat (lease, epoch, state, note)
- POST /jobs/{listingId}/images/{01-13}/start?commandId=... (image lease, epoch, attemptId)
- POST /image-commands/{commandId}/complete (lease, epoch, outcome, notes)
- POST /image-commands/{commandId}/yield (lease, epoch)
- POST /jobs/{listingId}/images/{01-13}?commandId=... (raw PNG)

All mutation routes require X-CAM-Pack-Id (64 hex) and X-CAM-Garment-Sku.
Command claim additionally requires X-CAM-Skill-Sha256. Command claim response
has command lease + epoch; image-claim uses that lease, returns imageLease.
Image grant rotates safely before any role-start; after start, no rotation.
Role-start sendAllowed=true only for first committed attempt grant. Heartbeat
renews command/image expiry and renderer reservation. STOP or capability
disable prevents new sends, not receipt/heartbeat/complete for existing work.
Yield only before any role was started. ACK replay does not release a newer
resource reservation. Lock order: capability (when needed), resource, flow,
batch, pack, role, command. Expired ownership requires explicit reconciliation.

Upload: application/image MIME is not accepted; Content-Type image/png.
Require X-Muse-Lease, X-CAM-Epoch, X-CAM-Attempt-Id, X-CAM-Image-Role,
X-CAM-Image-Sha256, X-CAM-Artwork-SHA256, X-CAM-Source-Sha256,
X-CAM-Placement-Lock-Sha256, X-CAM-Skill-Sha256, X-CAM-QA-Receipt-Id.
Max 32 MiB, PNG signature plus SHA, Pillow verify and full decode; square
native 1600..4096 subject to <=16 million pixels (existing Drive helper).
No resize, pad, crop, upscale. The server must resolve an actual accepted QA
record; worker self-assertion is not accepted. Commit upload intent before
provider I/O; exact replay returns 409 for reconciliation, no second write.
If provider response is lost, lease expires or commit fails, intent remains
pending, no repeat renderer call. Late receipt reconciliation still required.

Existing Gallery/Drive uses sp-<40hex> pack IDs; central 64-hex ID cannot
be used verbatim. Map to sp- + first 40 hex of SHA256("cam-image-orchestrator-
gallery-v1:" + full pack ID) without changing historical packs. Preserve
source_job_id "cam-image-" + full pack ID and check alias identity, immutable
slot, Picked tenant and deletion tombstone in the gallery transaction.
Trusted storage adapter reuses store_listing_output_to_drive and
read_design_from_drive, reads back and hashes original and thumbnail.
Role-specific optional storage contract selects corrected V13 role 01 and
04 filenames, prefixes central pack alias to avoid revision name collisions.
Legacy caller behavior preserved. Gallery projection and central receipt
commit atomically. No new persistent local image files. Native 1600 previews
do not receive a publication-ready manifest. Actual production manifest
and gallery deletion/status propagation remain pending; do not infer
publicationReady from central stored receipt alone before full integration.

Verification: 46/46 passed in isolated harness using installed dependencies
and in-memory SQLite: 12 P0, 10 persistence/migration/resource, 10 read/auth,
10 write API, 4 storage/gallery. HTTP tests simulate trusted QA/plan and
storage. Storage tests use actual gallery ORM models, fake provider I/O;
no live credentials, Drive, PostgreSQL, production database or renderer
calls. Migration 0217->0218 and empty rollback tested against SQLite.
No PostgreSQL server executable found on PATH (psql client only);
concurrent lock verification in an isolated PostgreSQL environment remains
NOT_RUN. No production activation, no worker changes, no video router/cron
changes. Actual live production baseline must be reconciled before release.

Remaining release blockers: real authoritative artwork/plan/palette and
QA approval adapters; live scheduler/discovery/contract and image-only
cooldown/skip reporting; reconcile expired commands and late Drive receipts
and persist isolated recovery batches; active gallery deletion and final
publication manifest semantics; verify shared resource participation of
video runner without modifying its production behavior; actual PostgreSQL
contention and full migration chain tests; live auth; legacy Extension drain,
Muse contract alignment and pilot. get_adapters() deliberately returns 503
until real adapters are bound. Read feature flag defaults off. Neither
changing a flag nor passing offline tests authorizes image generation.

## P2c recovery, PostgreSQL and trusted QA checkpoint

Source-only candidate on feat/cam-muse-hybrid-v1-20261125. Resolved the
previous worktree write conflict. Upload intent model, migration 0218 and
service now all require qa_receipt_id; replay must match it. Recheck
trusted QA/source approval after storage I/O and before committing role
and gallery receipt. Native PNG dimensions bounded consistently by
the 16-million-pixel validation ceiling.

New modules: admission.py, recovery.py, approvals.py. Migration 0219 adds
cam_image_plan_approvals and cam_image_qa_approvals, for ten new tables in
total across 0217-0219. Internal trusted authority records plan evidence
and image review evidence keyed by tenant, full pack, role, attempt,
epoch, worker, plan SHA and exact image SHA. Reject missing/revoked,
self-approved, held, unverified, stale or mismatched receipts. No renderer
write endpoint grants QA approval. Evidence validation is not an actual
visual reviewer; the trusted reviewer must inspect the exact image bytes
and verify source/placement/legacy drain before recording evidence. No
fake calibrated 150mm or live palette availability claims.

Admission validates the existing CAM C1717 SMS snapshot, exact source
descriptor, 150mm metadata lock and nine distinct configured colorways
(01-04 same base; 05-12 distinct alternates). Other SKU admission remains
disabled for this pilot. Recovery requeues only expired unsent commands
under the exact resource fence; started/uncertain uploads retain the
resource reservation and need trusted reconciliation. Isolated idle packs
may move into a recovery batch without changing their identity/role history.

New read-only candidate endpoints under /api/v1/muse-bridge:
GET /jobs/{listingId}/image-contract?packId=<64hex>&sku=C1717
GET /jobs/{listingId}/images/{role}/qa-status?packId=<64hex>&sku=C1717&attemptId=...&imageSha256=...
These enforce exact tenant and pack owner. Metadata only; contractReady,
rendererAllowed and uploadAllowed remain false. A genuine accepted QA
receipt ID can be retrieved only for the matching attempt/image; revoked
review is not accepted. No renderer can self-approve via these routes.

Verification: 64/64 isolated SQLite/TestClient tests, including seven
trusted-approval tests and two new read-contract/QA polling tests.
Six PostgreSQL 16.4 concurrent-transaction tests passed with migrations
0217->0219, including one-owner, resource fencing, role-start, upload
intent races, expired started command and simulated video reservation.
Disposable tmpfs container removed; no production DB or provider access.
Total 70 passed across the two test suites; actual QA, original download,
live Drive, live auth and shared live video participation not yet tested.
Production execution adapters remain unbound (503); read flag defaults
off; no production migration, new cron, activation or video changes.

Next release gates: actual independent reviewer/evidence ingestion and
legacy-drain proof, original-byte and live QA integration, real scheduler
discovery and trusted runtime wiring, late storage reconciliation and
Gallery deletion/manifest handling, resource arbitration participation
for both actual runners, migration-chain/restore and current release
integration verification, live worker auth, pilot. No new dependencies
or secrets. Keep image flow disabled; rollback before deployment by
omitting candidate changes, afterward preserve approval/receipt history
and disable admission. No downgrade of nonempty new approval tables.

## Muse 01 P2c acceptance and live legacy cutover audit (2026-11-25)

Muse reported workflow V13 tagfix SHA256
6e406f1bcadb3494c318d8ca94bdaaefbbf6db7d9fea7eef95c989192e4a7610
(11974 bytes), 23/23 offline checks, no real transport or renderer.
Video body V2.2 and both video/poller crons unchanged per Muse report.
PREPARED_DISABLED remains mandatory. The earlier QA journal write-lock
blocker is resolved in CAM source (0219 approvals); 70/70 latest CAM
tests include six real PostgreSQL contention tests. This is not a
production deployment or independent proof of Muse runtime behavior.

Created and executed read-only audit on DESKTOP-3FR0LF0,
D:\CAM Image Extension\scripts\audit-image-orchestrator-cutover-readonly.mjs.
Real local ledgers and authenticated existing production GET coverage and
pending-deletion APIs sampled 2026-11-25T13:32:13.915Z. Both HTTP 200;
local browser/pack/publisher journal digests unchanged during sampling.
No claim, upload, renderer, queue changes, credential output or service
restart. Results: 359 local packs, 358 C1717 packs for 334 distinct listings.
C1717 pack states completed 15, needs_review 101, running 167, queued 75.
1045 browser Skill/QA records: completed 971, deferred_review 65,
submission_unknown 6, superseded 2, queued 1. 832 saved-output publisher
ACK keys; zero revoked keys and zero eligible unsynced saved-output
candidates at sample. Existing CAM coverage contains 183 listing IDs with
at least one saved image (not per SKU/role/SHA). 151 distinct local C1717
listing IDs absent from any-image coverage. Existing deletion feed contains
277 pending slots, not 277 new products. Coverage does not prove exact
role equivalence, original bytes, current artwork, approved placement,
or SHA/QA status. Stable snapshot is not scheduler drain proof.
Do not infer pilot eligibility or automatically reset historical pack
states. No legacy Skill admission fence yet; cutoverAllowed=false.

Next action: establish Skill-only admission fence/drain without stopping
Inventory/Target, reconcile unknown attempts and saved-slot/explicit-delete
receipts at exact tenant/listing/pack/SKU/role/source/artwork SHA, produce
bounded eligible-pilot list. Keep image execution disabled until live
API/QA/resource/Drive and legacy cutover gates pass. The production
release workflow requires reviewed migration and rollback/restore evidence;
do not force deployment or restart active video solely to expose candidate
routes. No migration or deployment in this checkpoint.

## Legacy cutover hardening checkpoint

Live read-only audit of the existing Extension slot coverage client returned
HTTP 404 for /api/v1/etsy-spy/image-agents/{agent_id}/listing-image-slot-coverage.
Added the existing client-compatible read-only route in listing_images.py
and a bounded single-statement receipt projection in slot_coverage.py.
Tenant authorization reuses the paired Image Agent. Snapshot includes
Picked listings with no saved images, exact gallery identity, artwork and
source SHA, PNG SHA, native dimensions/preview flag, and the latest pending
deletion per slot. Refills suppress obsolete deletion receipts. No Drive IDs,
URLs, actual PNG bytes or credential output; this is saved receipt metadata,
not a fresh Drive check or QA approval. No migration needed for this route.
Five new tests pass against actual Gallery ORM tables in isolated SQLite;
69/69 CAM isolated suite passed. Previous six PostgreSQL concurrency tests
do not cover this new slot projection.

Extension source on DESKTOP-3FR0LF0 now reuses the existing Skill generation
pause, adds store-level admission/claim filtering and persistent pause on
restart, and avoids starving independent tasks after a paused candidate.
Active leases/results remain intact; account-wide quota is unchanged.
Backfill, main revision and automated missing-slot admission are guarded.
Exact source SHA mismatch blocks repair; strict cutover audit requires
sourceShaIncluded. scripts/audit-cutover-slots-readonly.mjs never applies
the plan or generates images. Eight new focused tests passed. Broader
combined Windows test run exited nonzero (includes stale UI-text assertion);
full regression and installed Bridge restart not yet verified. No claim of
full drain or completed cutover. Production API still old; do not deploy
coverage endpoint before verifying the installed Skill admission fence,
since the existing daily audit could otherwise schedule repairs automatically.
Do not activate Muse or change video cron. No production DB migration,
release, browser reload, live pause or worker change performed.
