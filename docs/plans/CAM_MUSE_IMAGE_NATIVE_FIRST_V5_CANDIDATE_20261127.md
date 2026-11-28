# Native-first V5 internal policy candidate — 2026-11-27

Scope: offline P2D worktree only. Production is on the independent Lite API
(single Role 01) and the existing 2000px gate is unchanged. The full P2D
migration/restore, live resource, source/Drive and worker adapter release gates
in CAM_IMAGE_ORCHESTRATOR_P2D_RELEASE_20261126.md remain in force.

Proposed policy for new explicitly operator-approved packs only:
native ImageGen first, native square 1600px minimum, six automatic visual
checks, V3 placement target rather than measurement certification. No required
blank/mask/geometry for a normal native render. Retain the 2000px default
for older packs. Source bytes, pack identity, original placement SHA, wave
barriers, receipt immutability and lease ownership are not relaxed.

native_first.py implements pure internal eligibility and method selection.
It cannot grant claims, verify actual pixels, authenticate the reviewer,
prove native origin, persist attempts, upload or publish. A trusted CAM
service must resolve the policy receipt, real attempt history, source,
native PNG and reviewer before invoking it; a worker-supplied boolean or
arbitrary SHA is not proof. Independent automatic QA need not be human QA.
Only Role 01 Hybrid is considered, and only after a reconciled fidelity
failure, once within the same three-attempt budget. Hybrid remains preview
only until separately admitted. Do not fork canonical role identity by
method, which would permit duplicate slots; bind method inside each attempt
and its receipt. Role 13 remains gated by approved dependencies.

No exact Muse V5 runner, workflow, registry or API changes document was
available in this session. The six internal check keys and Profile structure
are not asserted to match its wire schema. Obtain those actual files with
full SHA256 and representative request/response fixtures before binding
claim/start/status/upload/QA. No new endpoint is registered by this patch.
An authenticated automatic review adapter and server-side PNG/storage
admission must still be implemented and integration-tested.

Verification: test_native_first.py + test_policy.py + test_admission.py,
41 tests passed (synthetic/offline). No new migration, dependency or secret.
No production config, queue, cron or gallery change. No video change.
Rollback of the initial checkpoint: omit those files before integration.

## Adaptive Batch V6 server checkpoint

Implemented on the isolated P2D source branch, not production:
- Persist the verified-role baseline in each lease's existing JSON. A completed
  productive slice refunds the wave claim count exactly once, allowing nine
  individual second-wave slices. Empty/failed slices retain the five-claim
  bound, role attempts remain capped at three. Historical leases without
  baseline cannot be refunded. Replay never refunds twice.
- Command completion evaluates/persists the existing batch barrier inside the
  same transaction. No operator click between healthy waves. A separate
  scheduler still must periodically evaluate idle/expired batches.
- Internal nativeFirst plan profile requires an operator policy receipt digest
  in the immutable plan approval. Default legacy threshold 2000 unchanged.
  Only direct native with visual-estimate QA is admitted by the new path;
  Hybrid fallback remains unbound and preview-only.
- Native QA ledger validates full record against authoritative pack/role,
  attempt/epoch, image/source/artwork/placement/skill/plan/prompt SHA and
  independent reviewer identity. The trusted reviewer still has to inspect
  actual bytes; no worker self-approval route or new capability is exposed.
- Upload ticket and durable receipt carry resolution/method/QA/operator
  scope; re-resolve before accepting storage. A native 1600 under a strict
  2000 profile cannot enter production storage. Legacy receipt decoding
  supplies defaults for old JSON. Legacy preview projection unchanged.
- Existing canonical pack/role identities remain unchanged. No new migration,
  dependency, provider, secrets, video change or live claim.

Tests: 62 targeted passed; full image_orchestrator pytest 100 passed, 1 existing
Starlette/AnyIO deprecation warning. Two mock packs completed 26 unique
role/attempt grants through HTTP and isolated SQLite with one role per
slice, immediate storage acknowledgements and automatic global Wave 1
barrier. No real provider calls, no actual PNG visual QA/Drive integration.
Native 1600 ledger tests verify explicit policy proof, reject old/tampered
review records and altered receipt scope, and block 1600 under strict 2000.
PostgreSQL contention for these new changes, real Muse V6 compatibility,
live renderer/cron, authenticated automatic pixel-review adapter and
actual storage verification remain NOT_RUN.

Production release must first pass independent backup restore and reconcile
P2D with the latest production commit; migrations 0217-0219 not yet applied.
The current production Lite API must remain isolated. Obtain actual Muse V6
bundle/API fixtures, map the internal profile/receipt fields to the reviewed
wire contract and bind trusted runtime adapters. Do not present this
checkpoint as an activated scheduler or working live 13-image automation.
Rollback before deploy: revert this candidate's scoped source changes.
After any later release, stop claims first and retain all immutable receipts;
do not downgrade populated tables.

## 2026-11-28: native receipt discovery and adaptive PostgreSQL regression

- Read API reports completedRoles/totalRoles, and validated native QA/method,
  dimensions/policy and receipt SHA only for the exact owned pack/attempt.
  Disabled discovery still cannot grant a render or upload.
- Bounded image-only maintenance advances elapsed Wave 1 barriers for running
  tenants without releasing in-flight leases/resources or touching video.
  Installation of the periodic scheduler is separate.
- 102 isolated pytest tests passed on the prior test environment. Recovered
  the deleted test virtualenv with pinned repository direct dependencies in
  a single small separate environment; no production Python modifications.
- New p2d_adaptive_postgres_harness.py: 8/8 tests passed on disposable
  PostgreSQL 16.4 with actual 0217-0219 migrations. Two packs complete 26
  receipt-backed slots in one-role slices, simultaneous Wave 1 completion
  opens the barrier, replay is safe, and the deadline sweep preserves active
  resource/lease ownership. Initial test fixture lacked the mandatory gallery
  projection callback; fixed only the explicit synthetic fixture. Actual
  Drive, renderer, native visual review and live cron remain NOT_RUN.
  Temporary container removed; no live schema modification.
- Latest production inspected: 9f940c4e0, API active. It still has Lite modules,
  no central orchestration models. Common ancestor with P2D is 933918ca6.
  Release integration must preserve the newer Lite endpoints and video
  changes; no old worktree deploy. Exact V6 worker bundle still absent from
  available files. Independent off-host restore gate remains unverified;
  source VPS has only about 4 GiB available and is not a restore target.
