# CAM × Dola Render Gateway — Master Implementation, Mainline Development & VPS Deployment Guide

**Version:** 1.6  
**Date:** 2026-09-12  
**Primary repository:** `BaoNghiaNghia/creative-asset-manager`  
**Upstream:** `coll3879xx-cyber/dola-render-gateway`  
**Purpose:** Authoritative Codex taskbook and current-state record for the CAM × Dola/Seedance integration. v1.6 performs a fresh source/CI/remote-branch revalidation, confirms no CAM or upstream Dola source drift beyond the v1.5 SHAs, corrects the Windows Electron package result for CI run `34670741926` from SKIPPED to PASS, inventories all visible Dola integration branches, and preserves DG-05M as the required CI/storage/processing-policy reconciliation gate before backend integration.

> This document is both an implementation history and the active taskbook. Completed DG tasks are immutable evidence. Codex must execute **one active task per turn**, return the required report, then STOP. It must never automatically advance to the next task. **v1.6 revalidation confirms that current `main` is still `3a3f60f...`, still lacks the Dola integration, upstream Dola is still `ce1ebc0...`, and main CI is still red for the same deterministic non-Dola blockers. DG-05M remains the next task. Windows Electron packaging is independently green and must not be misreported as skipped.**

---

## What changed in v1.6

This revision is based on another fresh GitHub source/CI/branch recheck on 2026-09-12. There is **no new CAM or Dola source drift** after v1.5, but the audit record needed two corrections/clarifications:

```text
1. CAM main remains unchanged from v1.5:
   3a3f60f267b433742adec5a38f64f1d274843253
   fix(processing): recover stale concurrency counters

2. Dola upstream remains unchanged:
   ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4
   Update README.md

3. Main CI run 34670741926 is still RED. Revalidated job results are:
   FAIL  Frontend checks
   FAIL  API, worker and provider unit tests
   FAIL  PostgreSQL migrations and repositories (12.22)
   FAIL  PostgreSQL migrations and repositories (16.4)
   FAIL  Elasticsearch v2 integration
   PASS  Durable pipeline end-to-end
   PASS  Windows Electron package

4. Correction to v1.5:
   Windows Electron package was NOT skipped. It completed successfully,
   including desktop typecheck, tests, unsigned installer build, and artifact upload.

5. All visible Dola integration remote branches remain stale and do not contain
   the verified DG-00..DG-05 chain:
   feat/dola-render-gateway-integration        -> c20021c...
   feat/dola-render-gateway-integration-check  -> c20021c...
   feat/dola-render-gateway-integration-temp2  -> d2a42ef...
   feat/dola-render-gateway-integration-temp3  -> c20021c...
   feat/dola-render-gateway-integration-temp4  -> c20021c...

6. No architecture/task-order change is justified by the fresh source check.
   DG-05M remains:
   DG-05M-CI -> DG-05M-S -> DG-05M-P -> DG-05M-M -> DG-05M-I.
```

# 0. Verified baselines and current implementation status

This guide began from CAM `main` at `c20021c...`, but that SHA is now a **historical integration base**, not the current mainline.

Latest repository state verified on 2026-09-12:

```text
CAM repository:
BaoNghiaNghia/creative-asset-manager

Latest verified CAM main:
3a3f60f267b433742adec5a38f64f1d274843253

Latest verified CAM main message:
fix(processing): recover stale concurrency counters

Parent / previous v1.4 main:
b8695c931d8e63b1ca33e0f6691b9b7403acd092

Historical Dola-integration base:
c20021c4909cd270a1c4578cd047105d22df58d2

Dola upstream repository:
coll3879xx-cyber/dola-render-gateway

Latest verified Dola upstream:
ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4
```

The recorded SHAs are audit anchors only. Never reset newer work to them.

## 0.1 Mainline drift since the Dola branch base

`main` is currently five commits ahead of the historical Dola integration base `c20021c...`:

```text
c7e1afd343ae5461ca4db345fc6dcda7625bcc19
fix(storage): release staging after pipeline completion

4c8f5cf5a1b541f4f0ec6d6d56a8324ef8a4dd26
fix(storage): clean completed analysis staging under pressure

75b8e9b1fa0d95740dcea1821d5bd50afc689e37
feat(storage): schedule cleanup around image workload

b8695c931d8e63b1ca33e0f6691b9b7403acd092
fix(storage): schedule next adaptive cleanup batch

3a3f60f267b433742adec5a38f64f1d274843253
fix(processing): recover stale concurrency counters
```

Latest source revalidation confirms:

```text
current origin/main HEAD = 3a3f60f267b433742adec5a38f64f1d274843253
main migration head in source = 0066_video_gemini_backups
apps/api/app/modules/video_generation/ on main = absent
0067_video_generation_domain.py on main = absent
Dola/video-generation config flags on main = absent
main branch protection = OFF
```

Therefore **DG-00..DG-05 are not merged into current `main` yet**. The local verified DG commit chain remains the integration input; GitHub `main` remains the target.

The current-main drift touches two integration-sensitive domains:

```text
Managed Storage / cleanup / capacity:
  apps/api/app/core/config.py
  apps/api/app/modules/storage/**
  apps/api/tests/modules/storage/**

Processing concurrency accounting:
  apps/api/app/modules/processing_policy/claim.py
  apps/api/tests/modules/processing_policy/test_policy_and_claim.py
```

The completed DG backend commits must **not** be force-merged or blindly fast-forwarded over current `main`. The next task remains **DG-05M**, now with explicit CI, storage, and processing-policy reconciliation before any merge/push.

## 0.2 Completed Dola implementation ledger

The authoritative implementation evidence reported from the isolated DG worktree is:

```text
DG-00  b336ca1ec138a5faf368389e9f50ceed3c089f59  PASS
       chore(dola): vendor pinned render gateway source

DG-01  c2354fa32828aaaec9b7072c58376c70d01abf31  PASS
       feat(dola): harden isolated gateway runtime

DG-02  d41bebc33f9028ce14443a1f7753f167eb2bcf7c  PASS
       feat(dola): add idempotent internal generation API

DG-02A 890886c00b9d416521feb28e1d6e4336da9a113d PASS
       crash-state / submission_unknown acceptance gate

DG-03  87898d5084f1a32cf15c93bed0e227988f731ddc  implemented

DG-03A d85eab6a89ff0a1bef960e1eaca426340fbc60ad PASS
       test(video-generation): complete domain acceptance gates

DG-04  31f9102e8edd30c93ba2cff2e0bda44caaaaa472  PASS WITH FOLLOW-UP
       feat(video-generation): execute Dola jobs in video worker

DG-04A f6d4c93ead7d4d5be024cfca9fa0cca29ec4400f PASS
       worker async ownership/lifecycle acceptance gate

DG-05  adb91f3d48ce3a3fffd334190dad89a64e664516  PASS
       managed output import / recovery / result API / safe cancellation
```

No production action was performed by DG-00 through DG-05.

## 0.3 Implemented backend capability after DG-05

The isolated DG worktree now contains the backend path:

```text
CAM API
  -> video_generation_runs / references
  -> ProcessingJob(video_generate)
  -> CAM video worker
  -> authenticated loopback Dola Gateway
  -> Seedance execution
  -> authenticated /content stream
  -> bounded CAM local staging
  -> SHA-256 Asset Registry identity
  -> Managed Storage
  -> output_asset_id
  -> completed
```

Verified safety properties from the completed task reports:

```text
CAM PostgreSQL remains business authority.
Dola SQLite remains execution/recovery state only.
Dola submit uses stable Idempotency-Key: video-generate:<CAM run id>.
Lost submit response retries the SAME key.
Persisted gateway_generation_id causes GET-only reconciliation.
submission_unknown never triggers blind resubmission.
Dola completed transitions CAM to storing before import.
Content/storage failure never causes a second provider generation.
Completed output is served to browsers from CAM Managed Storage, not Dola.
Pre-submit cancellation is supported; provider-side post-submit cancel is not.
VIDEO_GENERATION_ENABLED and DOLA_RENDER_GATEWAY_ENABLED remain default OFF.
```

DG-05 also introduced bounded local video staging with:

```text
VIDEO_GENERATION_STAGING_ROOT=/var/lib/creative-asset-manager/video-generation
VIDEO_GENERATION_MAX_OUTPUT_BYTES=268435456   # 256 MiB
```

and strict generated output MIME `video/mp4`.

## 0.4 Migration status

The video-generation migration is:

```text
0067_video_generation_domain
```

DG-03A proved a temporary-DB round trip:

```text
0066_video_gemini_backups
  -> 0067_video_generation_domain
  -> 0066_video_gemini_backups
  -> 0067_video_generation_domain
```

with both video-generation tables appearing/disappearing as expected.

Before any integration or deployment, always rerun `alembic heads` against the actual current tree. Do not assume there can never be a newer migration after this document date.

## 0.5 Test status through DG-05

Completed integration-worktree acceptance evidence remains:

```text
DG-03A: 116 passed, 0 failed
DG-04A: all 23 runtime nodes covered 3x sequentially, all exit code 0;
        boundary/order/lifecycle gates passed
DG-05: 86 focused passes across shards, 0 failed;
       compileall PASS; git diff --check PASS
```

The DG-04A monolithic-runtime "hang" was reclassified as an execution-harness wall-clock cutoff, not a proven product deadlock. Deterministic sharded coverage plus direct thread/resource assertions proved worker async cleanup.

This evidence validates the isolated DG implementation, but it does **not** replace post-reconciliation testing against current `main`. DG-05M must rerun the overlap-sensitive migration, storage, cleanup, worker, API and default-off gates after the current-main storage commits are incorporated.

---

## 0.6 Remote feature-branch caveat

Do not assume any visible GitHub Dola integration branch contains the local DG commit chain. Fresh v1.6 revalidation found:

```text
feat/dola-render-gateway-integration
  c20021c4909cd270a1c4578cd047105d22df58d2

feat/dola-render-gateway-integration-check
  c20021c4909cd270a1c4578cd047105d22df58d2

feat/dola-render-gateway-integration-temp2
  d2a42ef753ea72c7a395d02d2d0a4350d11212eb

feat/dola-render-gateway-integration-temp3
  c20021c4909cd270a1c4578cd047105d22df58d2

feat/dola-render-gateway-integration-temp4
  c20021c4909cd270a1c4578cd047105d22df58d2
```

None of those refs reaches the locally reported verified DG chain, which continues through:

```text
adb91f3d48ce3a3fffd334190dad89a64e664516  # DG-05
```

Therefore DG-05M must use the **actual local verified commits/worktree** listed in section 0.2 unless those commits are explicitly pushed first. Never reconstruct the integration from any stale remote branch name, including the `-check` or `-temp*` refs.

## 0.7 Dola upstream revalidation

Latest upstream revalidation on 2026-09-12 found no new upstream commit beyond the recorded snapshot:

```text
coll3879xx-cyber/dola-render-gateway
latest verified upstream SHA:
ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4
message: Update README.md
```

Therefore the vendored upstream provenance baseline remains unchanged. Future work must still re-read current upstream before applying an update.

## 0.8 New source-development policy

The old v1.2 rule "do not develop directly on main" is retired **only after DG-05M succeeds**.

New rule:

```text
Before DG-05M:
  preserve the isolated DG worktree and verified commit chain.

DG-05M:
  classify the current-main CI baseline;
  reconcile DG-00..DG-05 with latest origin/main;
  resolve storage cleanup/capacity semantics explicitly;
  rerun affected release gates;
  integrate into main with all generation flags OFF.

After DG-05M PASS:
  develop DG-06 onward directly on clean latest main;
  do not create a new long-lived Dola feature branch;
  keep one focused task/commit gate at a time;
  never force-push main.
```

Current GitHub `main` is **not branch-protected**. Direct-on-main safety is therefore procedural, not enforced by GitHub. Before every future direct-main task:

```text
fetch origin
require clean local main
require local HEAD == origin/main before editing
run task-specific tests before commit
fetch origin again before push
never use --force / --force-with-lease on main
stop on non-fast-forward instead of overwriting remote work
```

Source integration into `main` is still **not** production deployment or feature activation.

## 0.9 Current main CI baseline — RED and freshly reclassified

Latest verified push CI for `main` SHA `3a3f60f...` is workflow run:

```text
run id: 34670741926
status: completed
conclusion: failure
head: 3a3f60f267b433742adec5a38f64f1d274843253
```

Current deterministic failure classification:

| CI job | Latest result | Current failure classification |
| --- | --- | --- |
| Frontend tests/build | FAIL | Lint/typecheck pass, then `SettingsStorageCleanupPolicyPanel` test `disables manual run when managed storage is off and enables it after toggling back on` expects the manual-run button disabled but receives enabled state. Reconcile intended managed-storage gating; do not merely weaken the assertion. |
| API and worker unit tests | FAIL | 7 setup errors share `NoReferencedTableError`: selected test metadata invokes `Base.metadata.create_all()` without registering `oauth_connections`, while `external_sources` has a tenant FK to it. Repair test model registration/fixture completeness; production FK stays intact. |
| PostgreSQL migrations/repositories 12.22 | FAIL | Downgrade reaches `0066_video_gemini_backups`; raw SQL contains `LIKE 'gemini_video_backup_%'`. psycopg 3 interprets `%` as invalid DBAPI placeholder syntax and raises `ProgrammingError`. |
| PostgreSQL migrations/repositories 16.4 | FAIL | Same deterministic `0066` psycopg placeholder failure as PG12. |
| Elasticsearch v2 integration | FAIL | Same metadata-registration class as API/unit: `Base.metadata.create_all()` cannot resolve `external_sources.tenant_id -> oauth_connections.tenant_id` because auth persistence model/table is not registered in the selected integration-test metadata. |
| Durable pipeline end-to-end | PASS | Green on current main. Preserve this. |
| Windows Electron package | PASS | Desktop typecheck and tests passed; the unsigned Windows installer was built and uploaded successfully in this run. This job is independently green even though the overall workflow is red. |

Important diagnosis rules:

```text
The PostgreSQL 0066 failure is a real current-main migration implementation/compatibility defect.
Do not use alembic stamp to bypass it.
Preserve the downgrade safety check while making the literal `%` psycopg-safe.

The Elasticsearch + API/unit oauth_connections failures are test metadata bootstrap defects.
Do not remove/weaken the production tenant FK to make isolated tests pass.

The frontend cleanup-policy failure is behavior-vs-test contract drift around a newly sensitive
managed-storage control. Establish intended behavior, then align implementation and test.

Durable pipeline E2E is currently green and must remain green after fixes/integration.
Windows Electron packaging is also green in this exact run and should remain green; do not gate its recorded result on unrelated failed jobs.
```

DG-05M-CI should repair these deterministic current-main failures before Dola source integration if practical. At minimum, any integrated-tree failure must be distinguishable from this exact baseline, but **DG-05M PASS should target a fully green deterministic CI baseline rather than normalize known code/test defects**.

## 0.10 Managed-storage semantic reconciliation newly required by latest main

Current `main` added adaptive Managed Storage staging cleanup/capacity behavior. Relevant defaults include:

```text
MANAGED_STORAGE_ENABLED=false
MANAGED_ASSET_STORAGE_ENABLED=false
MANAGED_STORAGE_STAGING_MAX_BYTES=0
MANAGED_STORAGE_STAGING_RETENTION_HOURS=168
MANAGED_STORAGE_CLEANUP_BATCH_SIZE=200
MANAGED_STORAGE_AUTO_CLEANUP_ENABLED=false
MANAGED_STORAGE_CLEANUP_INTERVAL_SECONDS=900
MANAGED_STORAGE_CLEANUP_BUSY_INTERVAL_SECONDS=3600
MANAGED_STORAGE_CLEANUP_OFFPEAK_INTERVAL_SECONDS=300
MANAGED_STORAGE_CLEANUP_PRESSURE_THRESHOLD_PERCENT=80.0
MANAGED_STORAGE_CLEANUP_CRITICAL_PRESSURE_PERCENT=95.0
MANAGED_STORAGE_CLEANUP_IMAGE_BACKLOG_THRESHOLD=10
```

Current-main cleanup scheduling is image-workload-aware, and storage-capacity accounting includes managed storage objects in `stored`, `uploading`, and `retry` states for the configured staging folder.

DG-05 final video results are **durable application outputs**, not disposable analysis staging. Therefore DG-05M must prove or correct the final-video storage topology before merge:

```text
final generated video must never be swept as temporary analysis staging
final generated video must not permanently strand bounded staging quota by being non-cleanup-eligible in a cleanup-only folder
capacity accounting for generated video must be intentional
already-stored crash recovery must remain idempotent
adaptive cleanup scheduling for existing image/video analysis must not regress
```

Preferred resolution if current DG-05 uses the same cleanup staging folder for final generated videos: store durable generated outputs in a durable managed-storage root/folder outside the cleanup staging pool, or introduce an explicit storage classification/policy with tests. Do **not** silently rely on incidental cleanup eligibility.


## 0.11 Processing concurrency-accounting reconciliation required by latest main

Commit `3a3f60f267b433742adec5a38f64f1d274843253` changes current-main processing-policy behavior for interrupted/stale jobs. Counter release now clamps stale tenant/category/provider active counters at zero and clears per-job `concurrency_accounted` state safely instead of permitting negative counters.

DG-05M must preserve these semantics for `video_generate`:

```text
P1 deferred video polling must not double-release concurrency accounting
P2 lease expiry / terminalization must release an accounted slot at most once
P3 concurrency_accounted must become false when release is complete
P4 stale/overcounted counter repair must clamp tenant/category/provider counters at zero
P5 counters must never become negative, including provider-scoped video_generate paths
P6 retries/recovery must not acquire a second logical slot for the same claimed state
P7 tests must use fake/local execution only; no Dola/provider network call
```

This is not permission to redesign processing policy. Reuse the current-main accounting contract and make the Dola handler/runtime conform to it.

---

# 1. Architecture decision

The target boundary is:

```text
ONE CAM GIT REPOSITORY       YES
ONE SOURCE OWNERSHIP TREE    YES

ONE PYTHON PROCESS           NO
ONE VENV                     NO
ONE SYSTEM USER              NO
ONE DATABASE                 NO
ONE SYSTEMD SERVICE          NO
ONE FAILURE BLAST RADIUS     NO
```

Core rule:

> **Shared source ownership, isolated runtime blast radius.**

Dola browser automation must never run inside the CAM API process or the CAM worker Python environment.

---

# 2. Target runtime architecture

```text
                         Internet
                            |
                            v
                     +--------------+
                     | Nginx :443   |
                     +------+-------+
                            |
                            v
                     +--------------+
                     | CAM API      |
                     | :8000        |
                     +------+-------+
                            |
                 +----------+-----------+
                 |                      |
                 v                      v
          PostgreSQL             CAM Processing Jobs
      business authority                |
                                         v
                                  CAM Video Worker
                                         |
                            localhost HTTP + Bearer
                                         |
                                         v
                        +-----------------------------+
                        | Dola Render Gateway         |
                        | 127.0.0.1:8100              |
                        +--------------+--------------+
                                       |
                                      Xvfb
                                       |
                                       v
                              Patchright Chromium
                              persistent VPS profile
                                       |
                                       v
                                    dola.com
                                       |
                                       v
                               local generated MP4
                                       |
                            authenticated content GET
                                       |
                                       v
                                 CAM Video Worker
                                       |
                                       v
                         CAM Managed Asset Storage
                                       |
                                       v
                              CAM Asset Registry
```

The personal desktop computer is not part of production runtime. Browser profiles live on the VPS.

---

# 3. Data ownership

## CAM PostgreSQL is authoritative for

```text
tenant/user ownership
prompt
reference asset relationships
generation lifecycle
provider task ID
output asset ID
user-visible errors
cancellation state
audit/business state
```

## Dola SQLite is only an execution ledger for

```text
local gateway task state
provider conversation recovery
account scheduling/usage state
local artifact metadata
gateway idempotency mapping
```

Dola SQLite must never become the CAM business source of truth.

---

# 4. Source ownership

Vendor a clean, pinned upstream snapshot:

```text
apps/
├── api/
├── client/
├── worker/
└── dola_render_gateway/
    ├── UPSTREAM.md
    ├── README.md
    ├── upstream/
    │   ├── server.py
    │   ├── config.py
    │   ├── store.py
    │   ├── browser.py
    │   ├── browser_pool.py
    │   ├── dola_client.py
    │   ├── video_worker.py
    │   ├── video_worker_ui.py
    │   ├── media.py
    │   ├── gap.py
    │   ├── add_account.py
    │   ├── requirements.txt
    │   ├── extensions/
    │   └── other required runtime files
    └── tests/
```

Do **not** use a Git submodule for V1.

Never vendor:

```text
.env*
accounts/
downloads/
*.db
*.sqlite*
browser profiles
cookies
debug screenshots
.venv/
__pycache__/
nested .git/
```

`UPSTREAM.md` must record repository URL, exact SHA, import date, imported inventory, local patch inventory, update procedure, and license/terms warning.

---

# 5. Important upstream observations to reverify

At the inspected Dola revision:

- create and poll endpoints are asynchronous;
- supported durations are 10/15/30 seconds;
- Seedance 2.0/2.5 aliases are supported;
- ratios include `16:9`, `9:16`, `1:1`, `4:3`, `3:4`;
- a new random `video_<uuid>` is created for each POST;
- task recovery is stored in SQLite;
- BrowserPool rotates persistent account profiles;
- extension-backed generation can force `headless=False`;
- reference images currently support public URL downloading;
- generated videos are exposed through a static `/videos` mount;
- an admin dashboard/API is mounted;
- default host is `0.0.0.0`;
- API auth can fail open to anonymous mode when keys are empty;
- default configured concurrency is 3;
- default proxy is `http://127.0.0.1:7890`;
- browser profile path is currently relative `./accounts`;
- pool usage DB is currently relative;
- the current account-add CLI accepts password/TOTP through argv and may print TOTP.

Codex must re-read current upstream source before applying patches.

---

# 6. Non-negotiable runtime isolation

Do not add these to CAM API/worker dependencies solely for Dola:

```text
patchright
Chromium binaries
browser extensions
browser profiles
Dola account cookies
```

CAM communicates with Dola only through loopback HTTP.

A regression test must prove that importing CAM API and CAM worker bootstrap does not import `patchright`.

---

# 7. No public Dola surface

Production must listen only on:

```text
127.0.0.1:8100
```

Never expose:

```text
0.0.0.0:8100
:::8100
public Nginx /dola proxy
public Dola dashboard
public /videos
```

Users access only CAM.

---

# 8. Internal authentication

Create a dedicated secret:

```text
DOLA_RENDER_GATEWAY_INTERNAL_KEY
```

Never reuse Visual Encoder, OAuth, database, or AI keys.

Production internal mode must be fail closed:

```text
blank key       => startup/readiness fails
missing bearer  => 401
wrong bearer    => 401
correct bearer  => allowed
```

Use constant-time comparison such as `hmac.compare_digest`.

Never place the key in logs, errors, SQLite, PostgreSQL, frontend code, diagnostics, or task payloads.

---

# 9. CAM settings

The implemented integration remains default-off.

Current required settings include:

```dotenv
VIDEO_GENERATION_ENABLED=false
DOLA_RENDER_GATEWAY_ENABLED=false
VIDEO_GENERATION_CANARY_TENANT_IDS=

DOLA_RENDER_GATEWAY_URL=http://127.0.0.1:8100
DOLA_RENDER_GATEWAY_INTERNAL_KEY=
DOLA_RENDER_GATEWAY_TIMEOUT_SECONDS=10
DOLA_RENDER_GATEWAY_CONTENT_TIMEOUT_SECONDS=300

VIDEO_GENERATION_POLL_SECONDS=10
VIDEO_GENERATION_STAGING_ROOT=/var/lib/creative-asset-manager/video-generation
VIDEO_GENERATION_MAX_OUTPUT_BYTES=268435456
```

Reference limits are defined by the implemented CAM/gateway contract and must be re-read from current source/capabilities before future changes. The implemented DG contract uses a maximum of **8 ordered JPEG/PNG/WEBP references**, **15 MiB per reference**, and **30 MiB aggregate**.

Important rules:

```text
empty canary allowlist => deny new generation work
empty Dola internal key while generation execution is enabled => fail closed
flags OFF => no video_generate execution and no Dola dependency required for startup
```

Prefer scoping the populated internal bearer key to the video-worker runtime rather than exposing it to frontend/browser code. The CAM API only needs normal CAM auth to create durable runs/jobs.

---

# 10. Dola production settings

Recommended production template:

```dotenv
DOLA_CAM_INTERNAL_MODE=1
DOLA_HOST=127.0.0.1
DOLA_PORT=8100
DOLA_INTERNAL_KEY=REPLACE_WITH_RANDOM_SECRET

DOLA_MAX_CONCURRENCY=1
DOLA_MAX_PENDING_TASKS=20

DOLA_VIDEO_TIMEOUT=300
DOLA_REFERENCE_VIDEO_TIMEOUT=900

DOLA_DB_PATH=/var/lib/dola-render-gateway/data/tasks.sqlite3
DOLA_POOL_DB_PATH=/var/lib/dola-render-gateway/data/pool_usage.sqlite3
DOLA_ACCOUNTS_DIR=/var/lib/dola-render-gateway/accounts
DOLA_DOWNLOAD_DIR=/var/lib/dola-render-gateway/artifacts

DOLA_PROXY=
DOLA_HEADLESS=1

DOLA_EXTENSION_ENABLED=1
DOLA_EXTENSION_DIR=/opt/dola-render-gateway/current/extensions/dola30

DOLA_REFERENCE_IMAGE_MAX_BYTES=15728640
DOLA_REFERENCE_IMAGE_MAX_COUNT=30
DOLA_LIMIT_RESET_TZ=Asia/Tokyo
```

Critical: explicitly set `DOLA_PROXY=` when there is no proxy service. Do not accidentally inherit upstream's loopback proxy default.

---

# 11. Persistent filesystem

```text
/var/lib/dola-render-gateway/
├── accounts/
├── data/
│   ├── tasks.sqlite3
│   └── pool_usage.sqlite3
├── artifacts/
├── debug/
├── browser-cache/
├── runtime/
└── tmp/
```

Owner:

```text
dola-render-gateway:dola-render-gateway
```

Use restrictive permissions; `accounts/`, `data/`, and debug output should normally be `0700`.

Never store persistent state under a Git checkout, `/opt/.../current`, or release directory.

---

# 12. Required persistence patch

Current upstream uses relative `accounts/` and default pool DB paths. Add:

```text
DOLA_ACCOUNTS_DIR
DOLA_POOL_DB_PATH
```

and use them consistently in:

```text
config.py
browser.py
browser_pool.py
server.py
operator account provisioning
```

Deployment/rollback must not change profile path.

---

# 13. Account provisioning security

Do not use the upstream production account-add CLI unchanged.

Create a hardened operator tool that:

- accepts only account alias on argv;
- reads email/password/TOTP secret using protected input (`getpass` as appropriate);
- never prints TOTP;
- never logs password/TOTP/cookies;
- never stores plaintext account credentials after session provisioning;
- writes profiles only under configured `DOLA_ACCOUNTS_DIR`;
- writes debug artifacts only under protected state directory when explicitly enabled.

If a human verification challenge occurs, stop and require operator intervention.

Do not add new CAPTCHA-bypass or stealth-evasion mechanisms as part of CAM integration.

---

# 14. Internal gateway contract

The hardened CAM-facing gateway contract implemented by DG-01/DG-02 is:

```text
GET  /health/live

POST /internal/v1/video-generations
GET  /internal/v1/video-generations/{generation_id}
GET  /internal/v1/video-generations/{generation_id}/content
```

`GET /health/live` is the only unauthenticated liveness route. Protected internal routes require the dedicated bearer key.

There is **no provider-side cancel endpoint in the current gateway V1 contract**.

The production bind is loopback-only by default:

```text
127.0.0.1:8100
```

The CAM HTTP client also rejects arbitrary non-loopback gateway targets for V1 so the bearer cannot be silently sent to a remote host.

---

# 15. Submit API

Headers:

```http
Authorization: Bearer <internal-key>
Idempotency-Key: video-generate:<cam-run-id>
```

The actual DG-02 multipart contract is:

```text
metadata      JSON part
references    repeated ordered file parts
```

The metadata carries the normalized generation inputs, including the current model, prompt, aspect ratio, and duration contract.

Current generation choices established by the CAM domain are:

```text
models:      seedance-2.0, seedance-2.5
ratios:      16:9, 9:16, 1:1, 4:3, 3:4
durations:   10, 15, 30 seconds
references:  0..8 ordered images
```

The response identifies the gateway generation and its state. Do not echo prompt/reference bytes in normal responses or logs.

---

# 16. Gateway idempotency — P1

Every internal submit requires the stable key:

```text
video-generate:<CAM video generation run id>
```

Gateway persistence uses a durable unique idempotency key and canonical request fingerprint.

Fingerprint semantics include:

```text
normalized model
normalized prompt semantics / prompt hash
aspect ratio
duration
ordered reference SHA-256 values
```

Behavior:

```text
same key + same fingerprint      => existing generation, idempotent replay
same key + different fingerprint => HTTP 409 conflict
```

The key must never incorporate attempt number, worker ID, timestamp, or random retry UUID.

Crash-safety rule from DG-02A:

```text
submission attempt becomes ambiguous after upstream/provider acceptance
=> gateway persists submission_attempted_at / submission_unknown semantics
=> no automatic blind provider resubmit
```

This is the primary duplicate-paid-generation protection.

---

# 17. Private reference transport

CAM production never persists public/signed reference URLs for Dola execution.

CAM video worker:

1. resolves tenant-owned CAM source content;
2. preserves the persisted reference order exactly;
3. enforces count/MIME/byte limits;
4. streams ordered multipart over loopback;
5. gateway validates again and stages only private local files;
6. browser automation uploads those private files;
7. gateway controls cleanup under its own runtime/state roots.

Implemented V1 limits:

```text
count:               <= 8
MIME:                image/jpeg, image/png, image/webp
per reference:       <= 15 MiB
aggregate references <= 30 MiB
```

No client-supplied local paths. No `file://`. Reference order is semantically significant.

---

# 18. Private output transport

Gateway output is retrieved only through:

```text
GET /internal/v1/video-generations/{generation_id}/content
```

Requirements:

```text
bearer authenticated
completed gateway generation only
video/mp4
private/no-store semantics
artifact path constrained to configured gateway artifact root
```

DG-05 CAM worker consumes this route with streaming HTTP and writes to bounded local CAM staging while hashing. It never loads the whole video into RAM and never uses an arbitrary provider video URL.

Browser clients do **not** call this gateway route. Completed browser playback comes from the CAM public `/video` endpoint backed by Managed Storage.

---

# 19. Cancellation semantics

Current V1 cancellation is intentionally limited because the Dola Gateway has no provider-cancel contract.

CAM public API supports safe cancellation only before provider submission:

```text
queued, no gateway_generation_id     => cancellable
preparing, no gateway_generation_id  => cancellable when still safe
submitted                            => 409
running                              => 409
submission_unknown                   => 409
storing                              => fail closed / no user cancel
completed/failed                     => terminal semantics
cancelled                            => idempotent cancelled response
```

Critical invariant:

```text
cancel accepted before provider submission
=> queued/leased worker later observes cancelled state
=> NO Dola POST
```

Do not claim provider-side cancellation or refund semantics that do not exist.

---

# 20. CAM video-generation module

Implemented module:

```text
apps/api/app/modules/video_generation/
├── __init__.py
├── model.py
├── schema.py
├── repository.py
├── service.py
├── gateway_client.py
├── handler.py
└── router.py
```

It follows CAM processing, asset-registry, managed-storage, and image-generation patterns without importing the Dola/Patchright runtime into CAM.

---

# 21. CAM database model

Migration implemented by DG-03:

```text
0067_video_generation_domain
```

Primary tables:

```text
video_generation_runs
video_generation_references
```

Business statuses:

```text
queued
preparing
submitted
running
submission_unknown
storing
completed
failed
cancelled
```

Important persisted identity/state:

```text
tenant/user/client_request_id idempotency identity
request fingerprint
provider = dola
model / prompt / aspect ratio / duration
gateway_generation_id
ordered reference identities
output_asset_id
bounded error information
timestamps
```

Constraints include tenant-aware Asset relationships and one logical run per tenant/user/client request identity. DG-03A proved migration round-trip and concurrency/idempotency acceptance.

---

# 22. CAM public API

Implemented backend contract after DG-05:

```text
GET  /api/v1/video-generations/capabilities
POST /api/v1/video-generations
GET  /api/v1/video-generations/{generation_id}
POST /api/v1/video-generations/{generation_id}/cancel
GET  /api/v1/video-generations/{generation_id}/video
```

Permissions:

```text
create/cancel: assets.generate
read/video:    assets.read
```

Create uses a durable `client_request_id` and ordered `reference_asset_ids` from existing CAM assets. The exact request/response fields must always be read from the current schema before frontend work.

Frontend/user APIs never expose the gateway URL, internal key, Dola account identity, cookie state, local browser profile path, or provider-local artifact path.

---

# 23. Triple idempotency

CAM API:

```text
tenant + user + client_request_id
=> one video_generation_run
```

CAM processing queue:

```text
job_type=video_generate
idempotency_key=video-generate:<run-id>
```

Dola gateway:

```text
Idempotency-Key=video-generate:<run-id>
```

Frontend DG-06 must preserve the same `client_request_id` across ambiguous retries of the same logical create request.

---

# 24. Tenant canary

New generation work requires all execution gates plus tenant eligibility:

```text
PROCESSING_JOBS_ENABLED=true
VIDEO_GENERATION_ENABLED=true
DOLA_RENDER_GATEWAY_ENABLED=true
MANAGED_ASSET_STORAGE_ENABLED=true
tenant present in VIDEO_GENERATION_CANARY_TENANT_IDS
```

An empty canary allowlist denies new video generation.

If a tenant becomes ineligible after provider submission, do not create new submissions. Existing persisted work may reconcile/poll/import safely according to its already-paid lifecycle.

---

# 25. Processing job integration

Implemented processing identity:

```text
JobType.VIDEO_GENERATE = "video_generate"
entity_type = "video_generation_run"
idempotency_key = "video-generate:<run-id>"
provider_key = "dola"
```

`video_generate` belongs to `VIDEO_WORKER_JOB_TYPES` and is excluded from the image worker.

Normal asynchronous provider waiting uses CAM's existing `DeferredJobOutcome`, so polling returns the job to the queue without consuming failure-attempt semantics.

---

# 26. DolaGatewayClient

Implemented CAM-owned HTTP client responsibilities:

```text
submit
GET status
stream content
```

No Dola runtime import is allowed.

Requirements implemented/retained:

```text
httpx.AsyncClient
loopback-only V1 target validation
fail-closed bearer auth
bounded timeouts
safe response parsing
stable submit idempotency key
streaming MP4 content
no prompt/reference/secret logging
worker-owned async event-loop lifetime
```

There is no gateway cancel method in V1.

---

# 27. VideoGenerateJobHandler state machine

Canonical implemented flow:

```text
queued
  -> preparing
  -> POST Dola with stable idempotency key
  -> persist gateway_generation_id
  -> submitted
  -> DeferredJobOutcome

submitted / running
  -> GET same gateway generation
  -> DeferredJobOutcome while non-terminal

submission_unknown
  -> GET-only reconciliation when gateway_generation_id exists
  -> NEVER blind POST

Dola completed
  -> CAM storing
  -> GET authenticated content for SAME gateway generation
  -> bounded local staging + SHA-256
  -> Asset Registry
  -> Managed Storage
  -> CAM completed
  -> ProcessingJob completed
```

If `gateway_generation_id` already exists, do not POST again.

If CAM is `submission_unknown` with no gateway ID, fail/defer safely for recovery; do not generate a replacement.

DG-04A established that worker-context async gateway work uses `WorkerAsyncExecutor`; per-job event loops are not used for the long-lived worker client.

---

# 28. Managed-storage completion

DG-05 implemented the durable output boundary in the isolated integration worktree.

CAM stages generated output locally under:

```text
/var/lib/creative-asset-manager/video-generation
```

with private directory/file permissions, `.tmp` streaming writes, byte counting, SHA-256, strict `video/mp4`, 256 MiB maximum output, and atomic replace.

The final identity flow is:

```text
same tenant + same SHA-256
=> reuse same Asset when already present

output_asset_id persisted
=> ManagedAssetStorageService
=> AssetStorageObject stored
=> run completed
```

Recovery invariants:

```text
storing + no local stage
=> refetch SAME Dola content, never POST

output_asset exists + storage not complete
=> resume storage, never POST

output_asset storage already stored + run still storing
=> mark run completed
=> no Dola GET
=> no second storage upload

completed retry
=> completed immediately
=> no Dola call
=> no second storage upload
```

The public CAM result endpoint streams from Managed Storage only:

```text
GET /api/v1/video-generations/{generation_id}/video
```

with tenant auth, `Cache-Control: private, no-store`, safe filename, and stored Asset MIME.

Known inherited limitation: if the underlying storage provider succeeds remotely but CAM crashes before persisting `stored`, remote-upload exactly-once behavior depends on the existing Managed Storage provider contract. This does not permit a second Dola generation and does not create duplicate CAM Asset/AssetStorageObject identities.

### v1.5 current-main storage reconciliation gate

Current `main` now has adaptive managed-storage cleanup and staging-capacity logic that did not exist at the historical Dola branch base. That changes one important assumption: **the storage destination used for final generated videos must be proven durable and semantically separate from cleanup staging**.

DG-05M must inspect how the integrated `ManagedAssetStorageService` selects `remote_folder_id` and prove all of the following:

```text
video-generation output is durable final content
video-generation output is not selected as disposable analysis cleanup
bounded staging quota cannot be permanently consumed by a final video that cleanup intentionally refuses to delete
already-stored video recovery remains no-Dola/no-second-upload
existing managed cleanup pressure/scheduling behavior remains unchanged for existing pipelines
```

If the current DG-05 implementation routes final generated video into `MANAGED_STORAGE_STAGING_FOLDER_ID`, treat that as a design question that must be resolved explicitly before merge. Do not mark DG-05M PASS solely because storage unit tests from the old branch still pass.

---

# 29. Error mapping

Use bounded machine codes, e.g.:

```text
dola_gateway_auth_failed
dola_gateway_unavailable
dola_rate_limited
dola_quota_blocked
dola_idempotency_conflict
video_generation_invalid
video_generation_storage_unavailable
```

Suggested behavior:

```text
401 local gateway       => config failure / non-retryable task, alert operator
connection/timeout       => retryable
429 known reset          => DeferredJobOutcome
409 idempotency conflict => non-retryable P1 invariant violation
invalid request          => non-retryable
storage temporary error  => retryable in storing state
```

Never use raw arbitrary provider exception text as a metric/error code.

---

# 30. Observability and privacy

CAM events may include:

```text
video_generation_created
video_generation_submitting
video_generation_submitted
video_generation_provider_running
video_generation_storing
video_generation_completed
video_generation_cancelled
video_generation_failed
```

Do not log:

```text
prompt text
reference bytes
Authorization
Dola internal key
Dola cookies
Google password
TOTP
signed URLs
raw browser storage
unredacted provider responses
```

Gateway diagnostics should expose aggregate task/account availability, not credentials.

---

# 31. Ubuntu runtime

Extension-backed Chromium may require a headed browser. Use Xvfb, not Ubuntu Desktop/GNOME.

Do not run Chromium as root.

Do not add `--no-sandbox` as a shortcut.

Production V1:

```text
DOLA_MAX_CONCURRENCY=1
```

Use multiple accounts for quota/health rotation, not automatic high parallelism.

Increase concurrency only after VPS resource and provider-risk evidence exists.

---

# 32. Dedicated system user

Recommended:

```text
user:  dola-render-gateway
group: dola-render-gateway
home:  /var/lib/dola-render-gateway
shell: /usr/sbin/nologin
```

CAM API should not read Dola profiles.

Normal communication is only loopback HTTP.

---

# 33. Independent runtime release

Source originates from one CAM Git commit, but deploy Dola into:

```text
/opt/dola-render-gateway/
├── releases/<cam-sha>/
└── current -> releases/<active-sha>
```

Persistent data stays under `/var/lib/dola-render-gateway`.

Use a Dola-only versioned/pinned venv, e.g.:

```text
/var/lib/dola-render-gateway/runtimes/<requirements-hash>/venv
```

Never install Patchright into CAM's API/worker venv or globally as a shortcut.


# 34. Proposed systemd unit

Codex must validate exact paths on the target VPS before activation.

```ini
[Unit]
Description=Creative Asset Manager Dola Render Gateway
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=dola-render-gateway
Group=dola-render-gateway

WorkingDirectory=/opt/dola-render-gateway/current
EnvironmentFile=/etc/dola-render-gateway/production.env
Environment=HOME=/var/lib/dola-render-gateway
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1

ExecStartPre=/usr/bin/test -r /etc/dola-render-gateway/production.env
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1920x1080x24 -nolisten tcp" /var/lib/dola-render-gateway/current-runtime/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8100 --no-proxy-headers

Restart=on-failure
RestartSec=5
TimeoutStartSec=90
TimeoutStopSec=60
KillSignal=SIGTERM
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/dola-render-gateway
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

# Initial guardrails only. Tune after actual measurements.
MemoryHigh=1500M
MemoryMax=2200M
CPUQuota=150%

[Install]
WantedBy=multi-user.target
```

Rules:

- verify `command -v xvfb-run` on the VPS;
- verify the exact Python/uvicorn path;
- validate Chromium under the systemd sandbox;
- do not blindly weaken hardening;
- resource limits above are starting guardrails, not measured production truth;
- no `PrivateDevices=true` unless browser compatibility is proven;
- do not expose port 8100 in Nginx/firewall.

---

# 35. Frontend integration — DG-06 (pending, after DG-05M)

DG-06 remains scheduled **after the one-time main reconciliation/merge** and is developed directly on latest `main` only after DG-05M passes.

Current client stack is React 18 + TypeScript + Vite + Vitest. Reuse the existing lazy `AppRoute.tsx` routing pattern; do not add React Router, Redux, React Query, or a new design system merely for this feature.

Required route/UX:

```text
/video-generation
/video-generation/<generation-id>
```

Required behavior:

```text
capabilities-first loading
prompt
server-driven Seedance model choices
server-driven aspect ratios
duration
0..capability maximum ordered CAM image references
visible reference ordering/reorder
one logical client_request_id per Generate action
safe retry of ambiguous create with SAME client_request_id
GET-only lifecycle polling
submission_unknown warning with NO provider-retry button
safe pre-submit cancel only
completed HTML5 video from CAM /video endpoint
```

Security boundaries:

```text
Frontend uses only /api/v1/video-generations...
No Dola internal URL
No 127.0.0.1:8100
No Dola bearer
No provider credentials
No Dola filesystem/profile state
```

Critical idempotency rule:

```text
ambiguous create HTTP failure
=> retry SAME payload + SAME client_request_id
=> never silently rotate UUID and create a new logical run
```

Completed playback must use the CAM Managed Storage result route, not Dola Gateway. If current CAM auth requires a custom header that native `<video src>` cannot carry, stop and resolve the auth/media contract rather than buffering a potentially large video into browser memory.

### Frontend build artifact policy corrected in v1.4

Current `main` CI explicitly rebuilds the client and requires `apps/client/dist/**` to match the committed distribution exactly. Therefore DG-06 **must not leave source and tracked dist out of sync**.

After source/tests pass:

```text
npm ci
npm run typecheck
npm test
npm run build
verify git diff -- dist is exactly expected generated output
commit synchronized source + required dist before pushing main
```

If using two local commits, a safe pattern is:

```text
1. feat(video-generation): add generation frontend
2. build(client): refresh video generation assets
3. push main only after BOTH commits exist locally and the final tree passes checks
```

Do not push the source-only commit first if it would knowingly make main CI fail. DG-07 no longer owns ordinary frontend dist refresh; DG-07 owns runtime/deployment artifacts.

---

# 36. License / service-terms release gate

At the inspected revision, the upstream README describes the code for educational/internal testing purposes.

Before commercial production or redistribution:

- confirm the upstream code permission/license as needed;
- confirm intended operation complies with Dola/provider terms;
- do not assume public GitHub visibility grants unrestricted commercial rights.

Codex cannot solve this gate by changing code.

Source architecture work may continue, but commercial production readiness must remain blocked while the gate is unresolved.

---

# 37. Security non-goals

This integration must not add:

```text
new CAPTCHA bypass
new anti-bot evasion
new browser fingerprint spoofing
cookie extraction for external reuse
provider account-security circumvention
```

If provider/Google requires human verification, surface the need to the operator and stop that provisioning attempt.

---

# 38. Reliability invariants

All must hold before a production canary:

```text
one CAM logical generation
=> at most one Dola/provider generation

worker restart
=> no duplicate provider submit

gateway restart after accepted provider conversation
=> resume, do not resubmit

CAM retry after lost POST response
=> same task via Idempotency-Key

storage retry
=> same CAM output asset

tenant removed from canary
=> no new submit, but in-flight work can drain safely

Dola down/browser crash
=> CAM API remains healthy

Dola dependencies broken
=> unrelated CAM jobs remain healthy
```

---

# 39. Source-development and branch strategy

The v1.2 long-lived feature-branch policy is superseded, but direct-on-main does **not** begin until DG-05M is complete.

## Phase A — completed isolated implementation

DG-00 through DG-05 were intentionally implemented in an isolated worktree/branch rooted at historical main `c20021c...` to protect active production work. That isolation phase is complete.

## Phase B — DG-05M one-time reconciliation

Current `main` is `3a3f60f...` and contains storage/config changes plus the newer processing concurrency-accounting recovery semantics, together with a currently red CI baseline. Perform one controlled reconciliation before any new frontend/deploy work.

Preferred method:

```text
actual local verified DG chain through adb91f3...
+ fresh clean temporary integration worktree from latest origin/main
+ classify current-main CI baseline
+ cherry-pick/reconcile verified DG commits in order
+ resolve config/storage overlap deliberately
+ prove durable generated-output storage topology
+ rerun migration/storage/cleanup/video-generation/worker/API critical tests
+ verify generation flags OFF
+ integrate/push main non-force only when gates pass
```

Never force-push `main`. Do not assume the stale remote feature branch contains the DG commits; use the actual local verified SHAs.

## Phase C — direct-on-main development

After DG-05M PASS:

```text
DG-06 onward starts from clean latest origin/main.
Do not create another long-lived Dola feature branch.
Each DG remains a focused implementation + tests + STOP gate.
Before editing: local main must equal origin/main.
Before push: fetch origin again.
If origin/main moved: STOP/reconcile; never rewrite remote history.
```

Because current `main` is unprotected, these checks are mandatory process controls rather than optional advice.

Direct-on-main development is a workflow choice, not permission to bypass tests, CI hygiene, build-artifact synchronization, or production safety.

---

# 40. Roadmap

```text
DG-00   Upstream provenance + vendored source boundary                         PASS
DG-01   Gateway hardening + persistent paths + secure runtime                  PASS
DG-02   Internal API + idempotency + private reference/content transport       PASS
DG-02A  submission_unknown crash-safety gate                                  PASS
DG-03   CAM domain + migration + public API                                    implemented
DG-03A  migration/concurrency/domain acceptance                               PASS
DG-04   CAM video worker + Dola submit/status polling                          PASS with follow-up
DG-04A  worker async ownership/lifecycle acceptance                            PASS
DG-05   content import + Managed Storage + completion/recovery/cancel/API       PASS
DG-05M  latest-main CI/storage/processing reconciliation + backend integration  NEXT; merge gated
DG-06   CAM frontend Video Generation UX + synchronized committed dist          BLOCKED on DG-05M
DG-07   Ubuntu/Xvfb/systemd/deployment artifacts + operator runbook             BLOCKED on DG-06 / latest main
DG-08   full source regression + security/reliability release review            BLOCKED on DG-06..DG-07
DG-10   VPS read-only readiness audit                                           BLOCKED on DG-08
DG-11   authorized runtime deployment with generation flags OFF                 requires explicit prod authorization
DG-12   explicit one-tenant production canary                                   requires separate explicit canary authorization
```

`DG-09` from v1.2 is retired/superseded. Its source-integration responsibility moved earlier and is now **DG-05M**, before frontend/deployment work.

Within DG-05M, treat these as explicit sub-gates rather than new long-lived branches:

```text
DG-05M-CI  repair/classify current-main deterministic CI failures
DG-05M-S   reconcile durable video output vs managed-storage staging cleanup/capacity
DG-05M-P   reconcile video_generate with current-main concurrency-accounting recovery
DG-05M-M   migration + code reconciliation + critical regression suite
DG-05M-I   non-force integration/push to main with generation flags OFF
```

---

# 41. Shared Codex startup protocol from v1.5 onward

For **DG-05M**:

```bash
git fetch origin
git rev-parse origin/main
git status --short
```

Use a clean temporary integration worktree from latest `origin/main`. Preserve the existing DG worktree and primary user checkout. Do not reset, stash, or clean unrelated user work.

Record before reconciliation:

```text
origin/main SHA
current-main CI run/conclusion
current main Alembic head
local DG-05 SHA
stale remote feature-branch SHA (informational only)
```

For **DG-06 and later direct-on-main source tasks after DG-05M**:

```bash
git fetch origin
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git status --short
```

Require before editing:

```text
branch = main
working tree = clean
HEAD == origin/main
```

If clean local main is behind, use only a safe fast-forward update such as `git pull --ff-only`. Do not rebase/rewrite published main history.

Before push:

```bash
git fetch origin
git rev-parse origin/main
```

If remote moved since task start, STOP and reconcile first. Never force-push or discard unrelated remote work. Current GitHub branch protection is OFF, so Codex must enforce these rules itself.

---

# 42. Shared forbidden actions for source-development tasks

Unless the task is explicitly DG-11/DG-12 with separate authorization, do not:

```text
SSH production
edit production.env
restart production API/worker
run production migrations
enable systemd services
provision real Dola accounts
launch production browser automation
submit a paid/real provider generation
modify production PostgreSQL/Elasticsearch
force-push main
```

Source-in-main is not production activation.

---

# 43. DG-00 — Upstream provenance and source boundary — COMPLETED

**Result:** PASS — commit `b336ca1ec138a5faf368389e9f50ceed3c089f59`. The instructions below are retained as historical/reproducibility guidance.

## Goal

Vendor the exact Dola source required by CAM while changing no CAM runtime behavior.

## Steps

1. Inspect the existing CAM checkout without modifying it:

```bash
git fetch origin
git rev-parse origin/main
git status --short
git branch --show-current
```

2. If the primary checkout is dirty, leave it untouched and create a separate clean worktree:

```bash
git show-ref --verify refs/heads/feat/dola-render-gateway-integration || true
git worktree list
```

If the branch does not exist:

```bash
git worktree add   -b feat/dola-render-gateway-integration   ../cam-dola-integration   origin/main
```

If the branch already exists, do not reset it. Inspect the existing branch/worktree and continue only if it is the expected DG work with a clean state.

3. Inside the DG worktree, require:

```text
branch = feat/dola-render-gateway-integration
working tree = clean
HEAD = intended current origin/main baseline
```

4. Resolve the exact upstream Dola commit.

The current verified upstream is still:

```text
ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4
```

If upstream `main` moved, do not silently vendor the newer revision. Vendor the audited SHA unless the newer revision is explicitly re-audited.

5. Import required files into:

```text
apps/dola_render_gateway/upstream/
```

6. Create:

```text
apps/dola_render_gateway/UPSTREAM.md
apps/dola_render_gateway/README.md
```

7. Add a scoped `apps/dola_render_gateway/.gitignore` for runtime state/secrets.

8. Do not wire CAM jobs/API yet.

9. Do not install Patchright, Chromium, Python dependencies, Node dependencies, or VPS packages in DG-00.

10. Commit only the vendored source boundary/provenance files, then STOP.

## Required checks

```text
primary dirty checkout modified by DG-00: NO
primary checkout stashed/reset/cleaned: NO
DG worktree clean before changes: YES

no nested .git
no .env / .env.*
no cookies
no profiles/accounts
no DB
no downloads/debug screenshots
no actual credentials

CAM runtime files changed: NO
CAM API imports Patchright: NO
CAM worker imports Patchright: NO
JobType changed: NO
config flags added: NO
migration added: NO
systemd changed: NO

git diff --check: PASS
staged diff limited to apps/dola_render_gateway/** (plus narrowly scoped docs/ignore only if required)
```

## Commit

```text
chore(dola): vendor pinned render gateway source
```

## Final report

```text
# DG-00 Result

## Baselines
- CAM origin/main:
- CAM baseline used:
- latest verified CAM audit: c20021c4909cd270a1c4578cd047105d22df58d2
- older CAM audit: d2a42ef753ea72c7a395d02d2d0a4350d11212eb
- Dola upstream current:
- Dola vendored SHA:

## Existing Checkout
- dirty before task:
- modified by DG-00: NO
- stashed: NO
- reset/clean performed: NO

## DG Worktree
- path:
- branch:
- starting SHA:
- clean before changes:

## Vendored Source
- path:
- file count:
- extensions included:
- nested .git: NO

## Provenance
- UPSTREAM.md:
- README.md:
- exact upstream SHA documented:
- update policy documented:
- license/terms gate documented:

## Secret / Runtime Safety
- .env committed: NO
- cookies committed: NO
- account profiles committed: NO
- DB files committed: NO
- downloads/artifacts committed: NO
- actual credentials found: NO

## CAM Runtime Impact
- API changed: NO
- worker changed: NO
- processing JobType changed: NO
- config flags added: NO
- migration added: NO
- systemd changed: NO
- recent Google auth/sidebar code touched: NO

## Verification
- git diff --check:
- compileall:
- staged diff check:
- working tree after commit:

## Commit
- DG-00 commit:

## Production Actions
NONE

## Verdict
PASS | PASS WITH CHANGES | BLOCKED

## Next Task
DG-01 READY | BLOCKED
```

STOP. Do not begin DG-01.

---

# 44. DG-01 — Gateway production hardening — COMPLETED

**Result:** PASS — commit `c2354fa32828aaaec9b7072c58376c70d01abf31`.

## Goal

Make the vendored gateway safe as a loopback-only Ubuntu service, without connecting CAM jobs yet.

## Required implementation

Add/configure:

```text
DOLA_CAM_INTERNAL_MODE
DOLA_INTERNAL_KEY
DOLA_ACCOUNTS_DIR
DOLA_POOL_DB_PATH
```

Make account/profile and DB paths absolute/configurable.

Internal mode:

```text
blank internal key => fail closed
missing bearer     => 401
wrong bearer       => 401
correct bearer     => protected routes allowed
```

Use constant-time comparison.

Add safe `/live` and `/ready`.

Protect or disable verbose legacy health/admin/static access in internal mode.

Do not rely on `DOLA_PROXY` default in production template.

Harden debug output and account provisioning:

```text
no password/TOTP argv
no TOTP print
no cookie print
no secret screenshots by default
```

## Tests

At minimum:

```text
internal blank-key behavior
auth missing/wrong/correct
/live privacy
/ready privacy
configured account dir in browser
configured account dir in pool
configured task DB
configured pool DB
restart paths stable
CAM imports unaffected
```

## Commit

```text
fix(dola): harden internal gateway runtime
```

## Final report

```text
# DG-01 Result
- internal auth:
- blank-key behavior:
- loopback policy:
- accounts path:
- task DB:
- pool DB:
- health exposure:
- legacy admin/static exposure:
- account provisioning secret handling:
- tests:
- commit:
- production actions: NONE
- verdict:
- DG-02 readiness:
```

STOP.

---

# 45. DG-02 — Internal contract, idempotency and private media — COMPLETED

**Result:** PASS — DG-02 commit `d41bebc33f9028ce14443a1f7753f167eb2bcf7c`, followed by DG-02A crash-state gate `890886c00b9d416521feb28e1d6e4336da9a113d`.

## Goal

Finish the internal gateway contract used by CAM.

## Required endpoints

```text
POST /internal/v1/video-generations
GET  /internal/v1/video-generations/{generation_id}
GET  /internal/v1/video-generations/{generation_id}/content
NO V1 GATEWAY CANCEL ENDPOINT
```

Refactor shared upstream submission logic instead of creating two independent orchestration paths.

Implement multipart private reference uploads.

Add TaskStore:

```text
idempotency_key
request_fingerprint
artifact_name
cancel_requested/failure fields as needed
```

## Mandatory idempotency tests

```text
first request => one task
same key + same request => same task, no second background generation
same key + changed prompt => 409
same key + changed model => 409
same key + changed reference bytes => 409
mapping survives TaskStore reopen/restart
```

## Mandatory reference tests

```text
no refs
valid JPEG
valid PNG
valid WEBP
oversize
invalid image
too many refs
client filename/path untrusted
temp cleanup
```

## Mandatory content tests

```text
unknown => 404
not completed => controlled 409
missing/wrong auth => 401
completed => bounded stream
path traversal impossible
artifact outside configured root rejected
```

## Recovery tests

```text
queued requeues once
accepted resumes without prompt resubmit
completed not recovered
failed not recovered
cancelled not recovered
```

## Commit

```text
feat(dola): add idempotent internal video contract
```

## Final report

```text
# DG-02 Result
- submit:
- status:
- idempotency:
- conflict:
- multipart refs:
- content:
- cancel:
- restart recovery:
- secrets in task DB: NO
- tests:
- commit:
- production actions: NONE
- verdict:
- DG-03 readiness:
```

STOP.

---

# 46. DG-03 — CAM video-generation domain, migration and API — COMPLETED WITH DG-03A

**Result:** DG-03 `87898d5084f1a32cf15c93bed0e227988f731ddc`; DG-03A PASS `d85eab6a89ff0a1bef960e1eaca426340fbc60ad` with 116 passing acceptance/regression tests.

## Goal

Add durable tenant-safe business state and CAM-facing APIs. No real provider call.

## Reconnaissance

Inspect current versions of:

```text
apps/api/app/modules/image_generation/
apps/api/app/modules/processing/
apps/api/app/modules/storage/
apps/api/app/modules/assets/
apps/api/app/core/config.py
apps/api/app/main.py
database/migrations/
authorization/viewer scope code
```

## Implementation

Create `apps/api/app/modules/video_generation/` with model/schema/repository/service/gateway-client interface/handler skeleton/router as appropriate.

Create a migration from the actual current Alembic head.

Add default-off settings/canary policy.

Add public CAM routes.

Use central tenant/auth permissions.

Create one `video_generate` processing job with:

```text
idempotency_key=video-generate:<run-id>
```

## Required tests

```text
capability disabled
capability enabled for eligible tenant
empty canary allowlist denies new work
API client_request_id idempotency
tenant A cannot read tenant B run
tenant A cannot reference tenant B source
Viewer/source scope preserved
deleted source rejected
migration upgrade
SQLite startup/migration regression
API startup regression
```

Do not run production migration.

## Commit

```text
feat(video): add durable Dola generation domain
```

## Final report

```text
# DG-03 Result
- origin/main observed:
- Alembic previous head:
- migration:
- run model:
- reference model:
- routes:
- capability:
- client idempotency:
- queue idempotency:
- tenant isolation:
- Viewer scope:
- tests:
- commit:
- production actions: NONE
- verdict:
- DG-04 readiness:
```

STOP.

---

# 47. DG-04 — CAM Video Worker and deferred Dola polling — COMPLETED WITH DG-04A

**Result:** DG-04 `31f9102e8edd30c93ba2cff2e0bda44caaaaa472`; DG-04A PASS `f6d4c93ead7d4d5be024cfca9fa0cca29ec4400f`. Worker gateway I/O now follows the worker-owned async executor lifecycle.

## Goal

Connect the existing video worker to the gateway while preserving process isolation.

## Required changes

```text
JobType.VIDEO_GENERATE
video_generate in VIDEO_WORKER_JOB_TYPES
global enablement flags
DolaRenderGatewayClient
VideoGenerateJobHandler
worker dependency/resource wiring
handler registry entry
```

Do not import upstream/Patchright in CAM runtime.

## State machine

```text
queued/preparing
 -> submit with Idempotency-Key
 -> persist gateway_generation_id
 -> submitted
 -> DeferredJobOutcome

submitted/running
 -> GET status
 -> running => DeferredJobOutcome
```

Never sleep for the provider generation duration inside the worker.

If `gateway_generation_id` is present, do not POST again.

## Tests

```text
video role owns job
image role does not
disabled startup needs no gateway
enabled + missing key fails at intended worker config boundary
submit persists task ID
running defers without consuming attempt
lost POST response retry uses same idempotency key
429 controlled/deferred
503 retryable
401 config error
Patchright absent from CAM imports
existing video analyze/search jobs still work
```

## Commit

```text
feat(video): execute Dola generations through video worker
```

## Final report

```text
# DG-04 Result
- job type:
- worker role:
- client:
- Patchright imported by CAM: NO
- submit:
- lost-response behavior:
- provider ID persistence:
- deferred polling:
- 429:
- 503:
- 401:
- tests:
- commit:
- production actions: NONE
- verdict:
- DG-05 readiness:
```

STOP.

---

# 48. DG-05 — CAM output storage, recovery, cancellation and errors — COMPLETED

**Result:** PASS — commit `adb91f3d48ce3a3fffd334190dad89a64e664516`; 86 focused passes, 0 failed.

## Goal

Complete the path from provider completion to a normal CAM video asset.

## Implement

```text
authenticated content streaming
output MIME/size validation
storing state
ManagedAssetStorageService
Asset Registry registration
output_asset_id
restart-safe store
duplicate-output prevention
cancellation
bounded error codes
```

## Required scenarios

```text
provider completes
gateway content timeout
gateway disappears after completed
artifact missing
bad MIME
oversize output
temporary managed-storage failure
restart during storing
restart after storage before final DB state
cancel queued
cancel submitted/running
provider output arrives after CAM cancel
```

Repeated execution must not create multiple output assets.

## Commit

```text
feat(video): persist Dola output as managed CAM asset
```

## Final report

```text
# DG-05 Result
- authenticated content:
- MIME:
- size limit:
- managed storage:
- asset registration:
- duplicate prevention:
- restart recovery:
- cancel:
- bounded errors:
- tests:
- commit:
- production actions: NONE
- verdict:
- DG-06 readiness:
```

STOP.

---

# 49. DG-06 — CAM frontend Video Generation UX — PENDING AFTER DG-05M

## Goal

Implement the browser workflow directly on **clean latest `main`** after DG-05M, using the DG-05 backend contract without changing backend/Dola code.

## Required UX

```text
/video-generation
/video-generation/<generation-id>

capabilities-first form
prompt
server-driven model/ratio/duration options
ordered 0..N existing CAM image references
Generate
safe create retry with SAME client_request_id
GET-only lifecycle polling
safe pre-submit cancellation
submission_unknown warning / no provider-retry button
completed HTML5 video from CAM /video endpoint
```

## Critical frontend invariants

```text
ambiguous create transport failure
=> preserve same logical request and same client_request_id

generation already exists
=> GET polling only
=> never auto-POST replacement

submission_unknown
=> safe GET reconciliation
=> no "Retry generation" action

completed media
=> /api/v1/video-generations/<id>/video
=> never Dola Gateway

reference order shown to user
=> exact same order in reference_asset_ids

tenant switch
=> cancel old poll + clear stale references/generation state
```

## Architecture

Use current React/Vite/Vitest client conventions and existing `AppRoute.tsx` lazy routing. Do not add a new router/state framework.

Before using a native `<video src>`, verify current CAM authentication is compatible with browser media requests. If auth requires a custom header unavailable to `<video>`, stop and resolve the contract rather than buffering a 256 MiB video into browser memory.

## Required checks

```text
focused Vitest feature tests
route/navigation regressions
reference ordering tests
client_request_id retry tests
poll lifecycle tests
cancel race tests
completed video-source tests
npm run typecheck
npm test (shard only if harness requires it)
npm run build
git diff --check
```

DG-06 is frontend-only. Do not modify `apps/api/**`, `apps/dola_render_gateway/**`, migrations, deployment services, or production configuration.

### Required tracked-dist synchronization

Current CI enforces that a production rebuild exactly matches committed `apps/client/dist/**`. Therefore DG-06 completion requires synchronized source and tracked dist.

Preferred local sequence:

```text
1. implement source/tests
2. run typecheck/tests/build
3. inspect generated dist
4. stage source/tests + the required generated dist changes
5. commit atomically OR make source/build commits locally without pushing between them
6. re-run final build-equality check
7. push main only when final tree is self-consistent
```

Do not leave dist refresh for DG-07.

## Commit

Accept either repository-consistent form:

```text
single commit:
feat(video-generation): add generation frontend

or two local commits before one push:
feat(video-generation): add generation frontend
build(client): refresh video generation assets
```

STOP after DG-06 PASS. DG-07 begins only afterward.

---

# 50. DG-07 — Ubuntu/Xvfb/systemd/deployment artifacts

## Goal

Commit everything necessary for a later authorized VPS deployment, while making no VPS mutation now.

DG-07 owns **runtime/deployment artifacts**, not routine frontend Vite `dist/**` synchronization. DG-06 must already have left client source and tracked dist in CI-consistent state.

## Required repository artifacts

At minimum:

```text
deploy/systemd/creative-asset-manager-dola-gateway.service
deploy/dola-render-gateway.env.example
deploy/tools/prepare_dola_runtime.sh
deploy/tests/...
docs/operations/DOLA_RENDER_GATEWAY.md
```

Update CAM deployment templates with video-generation flags.

If the internal key is scoped to video worker, document/update:

```text
/etc/creative-asset-manager/video-worker.env
```

and the video worker systemd template.

## Deployment tool properties

The runtime-preparation tool must:

- support safe validation/dry-run where practical;
- never contain real credentials;
- never read credentials from repo files;
- create/copy only the expected Dola runtime tree;
- pin dependency/runtime versions;
- verify state directories are external to release path;
- verify loopback configuration;
- not auto-enable video generation.

## Operator runbook

Document:

```text
Ubuntu prerequisites
dedicated user/group
Xvfb
Patchright/browser installation
browser cache
release/runtime layout
persistent state
env permissions
systemd
health verification
account provisioning
backup
rollback
upstream update
incident handling
```

Do not execute production commands in DG-07.

## Deployment-template tests

Prove:

```text
systemd unit references 127.0.0.1:8100
no Nginx Dola route
no real secret in env examples
concurrency default = 1
persistent paths use /var/lib/dola-render-gateway
service user is dedicated
video generation flags remain false
```

## Commit

```text
ops(dola): add isolated Ubuntu gateway deployment
```

## Final report

```text
# DG-07 Result
- systemd:
- env template:
- Xvfb:
- service user:
- persistent paths:
- runtime isolation:
- account provisioning docs:
- rollback:
- deploy template tests:
- frontend dist touched: NO unless a separate source change explicitly required it
- production mutation: NO
- commit:
- verdict:
- DG-08 readiness:
```

STOP.

---

# 51. DG-08 — Full source verification and release review

## Goal

Prove the whole feature branch is safe to integrate into `main` with all production flags OFF.

## Mandatory test areas

Run current relevant suites for:

```text
video_generation
Dola gateway
processing jobs
worker roles
worker bootstrap/runtime
managed storage
asset registry
tenant authorization
Viewer/source scope
image generation regressions
video search regressions
shared API startup
migration startup
frontend
TypeScript
configured lint/format
deployment tests
git diff --check
```

No real Dola account or Seedance generation is required.

## Security review

Explicitly verify:

```text
gateway production bind loopback-only
no Nginx proxy to 8100
internal blank key fails closed
no secret in frontend
no account credential in CAM
no prompt/reference bytes in gateway completion logs
no signed reference URL persisted for CAM flow
content path traversal blocked
duplicate provider submit blocked
Patchright not imported by CAM API/worker
flags default off
```

## Migration review

Verify:

```text
one expected Alembic head
correct down_revision
upgrade chain
SQLite startup remains valid
no destructive unrelated schema change
```

## Dependency review

Show separate dependency surfaces:

```text
CAM API/worker dependencies
Dola-only dependencies
browser runtime
```

No Dola browser dependency may leak into CAM requirements.

## Release conclusion

Source review may state:

```text
production runtime capacity UNKNOWN until DG-10
```

Do not claim production throughput or reliability based on mocked tests.

## Final report

```text
# DG-08 Release Review

## Source
- origin/main:
- branch:
- DG commit range:

## Gateway
- provenance:
- auth:
- persistent state:
- idempotency:
- private refs:
- private output:
- recovery:
- cancellation:

## CAM
- API:
- capabilities:
- tenant isolation:
- migration:
- processing job:
- video worker:
- deferred polling:
- managed storage:
- output asset:
- frontend:

## Isolation
- same Git repo: YES
- separate process: YES
- separate venv: YES
- separate system user: YES
- separate DB: YES
- loopback-only template: YES
- CAM imports Patchright: NO

## Tests
- gateway:
- backend:
- worker:
- auth/tenant:
- migration:
- frontend:
- deploy:
- total passed:
- failed:
- skipped:

## Security
- secret committed: NO
- provider public surface used by CAM: NO
- duplicate submit protection:
- path traversal:
- blank-key fail closed:

## Production
- flags default off: YES
- production actions: NONE
- real generation: NO
- VPS resource evidence: NO
- legal/terms gate: RESOLVED/UNRESOLVED/UNKNOWN

## Verdict
PASS / PASS WITH CHANGES / BLOCKED

## Post-review Production Readiness
DG-10 READY / BLOCKED
```

STOP.

---

# 52. DG-05M — Reconcile completed Dola backend with latest main and integrate source

**This is the current next task. Integration/push is gated by current-main CI classification and storage semantics.**

DG-05M replaces the old DG-09 source-integration stage. The integration is intentionally moved earlier so DG-06 onward can be developed directly on `main`.

## Preconditions

```text
DG-00..DG-05 implementation commits exist locally as listed in section 0.2
DG-03A PASS
DG-04A PASS
DG-05 PASS
no production action is required
```

Latest verified `main` at this guide revision:

```text
3a3f60f267b433742adec5a38f64f1d274843253
fix(processing): recover stale concurrency counters
```

Latest verified Dola upstream remains:

```text
ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4
```

Re-fetch both at execution time; newer source wins.

## Verified DG commit series

Apply/reconcile in this order from the authoritative local worktree:

```text
b336ca1ec138a5faf368389e9f50ceed3c089f59
c2354fa32828aaaec9b7072c58376c70d01abf31
d41bebc33f9028ce14443a1f7753f167eb2bcf7c
890886c00b9d416521feb28e1d6e4336da9a113d
87898d5084f1a32cf15c93bed0e227988f731ddc
d85eab6a89ff0a1bef960e1eaca426340fbc60ad
31f9102e8edd30c93ba2cff2e0bda44caaaaa472
f6d4c93ead7d4d5be024cfca9fa0cca29ec4400f
adb91f3d48ce3a3fffd334190dad89a64e664516
```

Do not use the stale remote feature-branch pointer as the source of truth.

## Phase 0 — capture current-main baseline before integration

Before cherry-picking anything:

1. `git fetch origin` and record exact `origin/main`.
2. Record current `alembic heads`/source migration head.
3. Inspect latest `main` CI run and record every job result/failure signature.
4. Confirm `main` still lacks `apps/api/app/modules/video_generation` and migration `0067_video_generation_domain`, unless newer main changed this.
5. Confirm whether `main` branch protection is enabled. At the v1.6 audit it was **OFF**.
6. Inspect `apps/api/app/modules/processing_policy/claim.py` and its concurrency-accounting tests because `3a3f60f...` changed stale-counter recovery after v1.4.

The v1.6 baseline failures are listed in section 0.9. If newer main fixed any of them, use the newer evidence and update the task report rather than preserving stale assumptions.

## Phase CI — DG-05M-CI: repair current-main deterministic CI defects

Target before merging DG source:

```text
current main deterministic CI = GREEN
```

Work from a clean current-main worktree and repair only proven defects. Current v1.6 signatures:

```text
CI1 PostgreSQL 12.22/16.4:
    0066_video_gemini_backups downgrade raw SQL LIKE literal contains `%`;
    preserve the credential safety check, make it psycopg-3 safe, and prove round-trip.

CI2 Elasticsearch integration:
    register/import the missing auth persistence model/table needed by metadata setup;
    do not weaken external_sources -> oauth_connections tenant FK.

CI3 API/worker unit fixtures:
    fix the same selected-model metadata-registration completeness issue for affected tests;
    no production schema weakening.

CI4 Frontend:
    reconcile SettingsStorageCleanupPolicyPanel manual-run disable semantics with
    managed-storage enablement; keep source and committed dist synchronized when build changes.

CI5 Durable pipeline:
    preserve the currently green end-to-end job.
```

Acceptance:

```text
C01 PG 12.22 full migration/repository CI path passes
C02 PG 16.4 full migration/repository CI path passes
C03 0066 upgrade/downgrade safety check preserved; no alembic stamp
C04 Elasticsearch integration passes with production FK unchanged
C05 API/worker unit suite passes without weakening schema/FKs
C06 frontend cleanup-policy contract/test passes
C07 Durable pipeline E2E remains green
C08 Windows packaging may execute only after prerequisites are green; if it then fails, classify/fix that new signature
C09 full push CI green, or only a clearly external/transient infrastructure failure with evidence
```

Do not call deterministic code/test failures "unrelated and ignorable" merely because they predate Dola integration. They are now part of the mainline gate.

## Phase S — mandatory managed-storage semantic reconciliation

Current main has adaptive cleanup/capacity behavior. Before merge, inspect the integrated behavior of:

```text
apps/api/app/core/config.py
apps/api/app/modules/storage/repository.py
apps/api/app/modules/storage/service.py
apps/api/app/modules/storage/managed_cleanup.py
apps/api/app/modules/storage/managed_cleanup_handler.py
apps/api/app/modules/storage/managed_cleanup_scheduler.py
apps/api/app/modules/video_generation/handler.py
```

Prove or correct:

```text
S1 generated final MP4 is durable output, not cleanup staging
S2 cleanup candidate selection never deletes final video-generation output
S3 if final video shares MANAGED_STORAGE_STAGING_FOLDER_ID, bounded quota cannot be stranded forever
S4 storage pressure accounting for generated output is intentional
S5 image/video analysis cleanup behavior remains unchanged
S6 already-stored video recovery still performs no Dola GET and no second upload
S7 storage retry never causes provider resubmission
S8 default storage cleanup flags remain OFF
```

If DG-05 final outputs currently use a cleanup-only staging folder with no safe lifecycle, modify the integration narrowly before merge. Prefer durable final storage separate from temporary cleanup staging or an explicit classified policy with tests.

## Phase P — DG-05M-P: processing concurrency-accounting reconciliation

After CI hygiene and storage semantics are understood, reconcile `video_generate` with current-main processing-policy recovery behavior. Inspect at minimum:

```text
apps/api/app/modules/processing_policy/claim.py
apps/api/tests/modules/processing_policy/test_policy_and_claim.py
apps/api/app/modules/processing/**
apps/api/app/domain/processing/**
apps/api/app/modules/video_generation/**
```

Required invariants:

```text
P01 stale tenant/category/provider counter release clamps at zero
P02 video_generate deferred polling does not consume attempts solely for polling
P03 deferred polling does not double-release concurrency slots
P04 lease expiry / failed / cancelled terminalization clears concurrency_accounted exactly once
P05 repeated cleanup/recovery calls remain idempotent with counters >= 0
P06 provider-scoped Dola counters follow the same accounting contract
P07 submission_unknown / storing recovery never reacquires or releases an extra slot incorrectly
P08 processing tests perform no Dola/Seedance/browser network execution
```

Add focused regression coverage for a `video_generate` job whose persisted active counter is already zero/stale when release runs. The result must remain zero, not negative, and job accounting state must be consistent.

Do not bypass current-main processing policy by special-casing Dola outside the shared accounting machinery unless a proven architecture constraint requires it and the guide is explicitly amended.

## Phase M — code/migration reconciliation

1. Preserve current DG worktree and all user checkouts. No stash/reset/clean of unrelated work.
2. Create a fresh temporary integration worktree from exact latest `origin/main`.
3. Cherry-pick/reconcile the verified DG commits in order.
4. Resolve conflicts by preserving **both** current-main behavior and DG safety invariants; never blindly choose ours/theirs.
5. Re-check at minimum:

```text
apps/api/app/core/config.py
apps/api/app/modules/storage/**
apps/api/app/modules/video_generation/**
apps/api/app/modules/processing/**
apps/api/app/modules/processing_policy/**
apps/api/app/domain/processing/**
database/migrations/versions/
.github/workflows/ci.yml   # inspect; change only if fixing proven CI-policy defects
```

6. Run `alembic heads` and require one expected head.
7. First prove current-main migration hygiene, including the repaired `0066_video_gemini_backups` downgrade under psycopg-backed PostgreSQL 12.22 and 16.4 CI. Then prove the video migration against the integrated source graph. Preserve the semantic round trip:

```text
0066_video_gemini_backups
-> 0067_video_generation_domain
-> 0066_video_gemini_backups
-> 0067_video_generation_domain
```

Use isolated temporary acceptance databases/configs only. Do not touch a real development/production DB and do not `stamp` around failures.
8. Run DG-05 critical tests plus current-main managed-storage/cleanup tests.
9. Run worker/runtime/default-off/API startup regressions, including DG-05M-P concurrency-accounting cases.
10. Run focused CI-hygiene tests for any baseline issues touched by reconciliation.
11. Run `git diff --check`, compile checks and secret/runtime-artifact scans.
12. Verify:

```text
VIDEO_GENERATION_ENABLED=false
DOLA_RENDER_GATEWAY_ENABLED=false
VIDEO_GENERATION_CANARY_TENANT_IDS empty
MANAGED_STORAGE_AUTO_CLEANUP_ENABLED remains safe/default OFF unless unrelated main policy says otherwise
no real bearer/account secret committed
no Dola public route added to CAM/Nginx
```

## Phase I — integrate to main safely

1. Fetch `origin/main` immediately before integration.
2. If it moved, STOP, reconcile against the new SHA, and rerun affected tests.
3. Integrate into local `main` without rewriting history.
4. Because `main` is currently unprotected, use process controls: **no force push, ever**.
5. Push only after the final local tree passes DG-05M gates.
6. Verify remote `main` contains the expected integrated content.
7. Observe push CI and compare against recorded baseline signatures.
8. DG-05M PASS requires no new integration failures; preferred state is fully green CI.
9. Remove only temporary clean worktrees created by DG-05M.

## Mandatory regression matrix

```text
C01 current-main deterministic CI defects repaired/classified
C02 PG12.22 + PG16.4 migration/repository jobs pass without stamp
C03 Elasticsearch + API/unit metadata bootstrap complete; production FKs unchanged
C04 frontend cleanup-policy behavior/test contract green
C05 Durable pipeline E2E remains green

S01 final generated MP4 has intentional durable storage semantics
S02 cleanup cannot delete final video-generation output
S03 final outputs cannot permanently strand staging quota
S04 capacity reservation/release accounting remains intentional and idempotent
S05 existing adaptive cleanup behavior remains green

P01 stale concurrency counters clamp at zero
P02 video_generate deferred polls do not double-release slots
P03 lease expiry/terminalization clears concurrency_accounted once
P04 tenant/category/provider counters never negative
P05 processing recovery tests make no real Dola/provider calls

M01 one expected Alembic head
M02 0066 -> 0067 -> 0066 -> 0067 isolated proof
M03 video-generation domain/API tests green
M04 Dola gateway client/handler tests green
M05 worker runtime/roles/bootstrap green
M06 ManagedAssetStorageService green
M07 ManagedStorageRepository green
M08 managed cleanup service/scheduler/handler green
M09 no provider resubmit after content/storage failure
M10 default-off config green
M11 API import/startup green
M12 secret/runtime artifact scan clean
M13 integrated tree introduces no new CI failure signature
```

## Forbidden

```text
no force push
no production env edit
no production migration
no service restart
no Dola account provisioning
no real Dola/Seedance generation
no feature flag activation
no stamp-around migration proof
no real development/production DB mutation for acceptance proof
no weakening/removing production FKs to satisfy isolated tests
no deleting/skipping tests merely to make CI green
```

## Final report

```text
# DG-05M Main Reconciliation Result

## Baseline
- local DG-05 commit:
- origin/main before:
- Dola upstream:
- main branch protected:
- main migration head before:
- latest-main commits reviewed:

## Pre-Integration CI Baseline
- workflow run:
- frontend:
- API/unit:
- PostgreSQL 12:
- PostgreSQL 16:
- Elasticsearch:
- pipeline E2E:
- Windows package:
- baseline red signatures:

## Integration
- method:
- temporary worktree:
- conflicts:
- config overlap resolution:
- storage overlap resolution:
- final-video durable destination/policy:
- migration head:
- integration SHA:

## Storage Semantics
- final video cleanup eligible:
- final video can strand staging quota:
- capacity accounting intentional:
- capacity reservation/release idempotent:
- existing cleanup behavior preserved:
- already-stored recovery no Dola/no second upload:

## Processing Concurrency Semantics
- stale counters clamp at zero:
- video_generate deferred poll double-release:
- lease expiry clears concurrency_accounted exactly once:
- tenant/category/provider counters non-negative:
- provider/network calls in policy tests: NONE

## Tests
- video-generation:
- managed storage:
- cleanup scheduler/handler/service:
- processing/runtime:
- processing-policy concurrency recovery:
- migration PG12/PG16:
- 0066->0067 round trip:
- API startup:
- frontend cleanup policy:
- baseline CI hygiene tests:
- passed:
- failed:

## Default-Off Safety
- VIDEO_GENERATION_ENABLED=false:
- DOLA_RENDER_GATEWAY_ENABLED=false:
- canary empty:
- real secret committed: NO

## Remote
- origin/main moved during task:
- push:
- force push used: NO
- final origin/main:
- post-push CI:
- new CI failure signatures introduced: NO | YES

## Production Actions
NONE

## Verdict
PASS | BLOCKED

## Next Task
DG-06 READY ON MAIN | BLOCKED
```

STOP. Do not begin DG-06 automatically.

---

# 53. Meaning of DG-05M PASS

After DG-05M PASS:

```text
verified Dola backend source in main                  YES
current-main storage cleanup semantics reconciled     YES
current-main processing concurrency semantics preserved YES
current-main deterministic CI baseline green             YES
integration introduced new CI failure signatures          NO
future long-lived Dola development branch needed      NO by current policy
production deployed                                   NO
production migration executed                         NO
Dola service installed                                NO
Dola account provisioned                              NO
video generation enabled                              NO
```

Preferred and expected DG-05M exit state is green push CI. A truly external/transient infrastructure incident may be documented separately, but the deterministic v1.6 failures in section 0.9 are repair items, not accepted permanent baseline reds. Do not proceed into routine direct-on-main development while deterministic mainline CI remains red.

DG-06, DG-07, and DG-08 then proceed as focused work directly on latest `main`.

---

# 54. DG-10 — VPS read-only readiness audit

Run only after DG-06, DG-07, and DG-08 have passed on `main`, so the audited source and deployment artifacts match what is intended for production.

This task is read-only.

If SSH access is unavailable, report the exact missing input and STOP. Do not scan ports, guess users, or try alternate credentials.

## Evidence to collect

```text
Ubuntu version
architecture
CPU
RAM
swap
disk free
load
active CAM release SHA
CAM release/current symlink
API status
video worker status
PostgreSQL status
Elasticsearch status
existing listeners
port 8100 collision
xvfb-run presence
Python version
browser/runtime prerequisites
available state directory capacity
```

Safe commands may include:

```bash
uname -a
cat /etc/os-release
uname -m
nproc
free -h
swapon --show
df -h
uptime
systemctl status <known units> --no-pager
systemctl cat <known units>
ss -ltnp
command -v xvfb-run
python3 --version
```

Do not install anything.

Do not modify any file.

Do not print the full production environment.

## Capacity evidence

Measure current:

```text
CAM API RSS/CPU
video worker RSS/CPU
Elasticsearch memory/heap
available system memory
disk
load
```

Decide only whether there appears to be headroom for a concurrency-1 canary.

Do not fabricate values.

## Final report

```text
# DG-10 VPS Readiness

- Ubuntu:
- architecture:
- CPU:
- RAM:
- swap:
- disk:
- load:
- active CAM SHA:
- API:
- video worker:
- PostgreSQL:
- Elasticsearch:
- xvfb-run:
- Python:
- port 8100:
- browser dependencies:
- resource headroom:
- mutations: NONE

## Verdict
READY / READY WITH CHANGES / BLOCKED

## DG-11 Readiness
READY / BLOCKED
```

STOP.

---

# 55. DG-11 — Authorized VPS runtime deployment, generation OFF

This guide does **not** itself authorize DG-11.

A separate explicit production instruction is required, for example:

```text
Authorize DG-11 production deployment. Keep all video-generation feature flags OFF.
```

Resolve the legal/terms gate before commercial production.

## Deployment sequence

1. Record current CAM/Dola rollback baseline.
2. Back up relevant config safely.
3. Create dedicated Dola system user/group if absent.
4. Create persistent state directories/permissions.
5. Copy the exact integrated Dola source from CAM release to `/opt/dola-render-gateway/releases/<sha>`.
6. Build/select pinned Dola-only venv/runtime.
7. Install Xvfb/browser OS prerequisites only if authorized and missing.
8. Install exact Patchright browser revision.
9. Create root-protected `/etc/dola-render-gateway/production.env`.
10. Generate new random internal bearer.
11. Put the matching key in the video-worker-only secret environment.
12. Keep:

```text
VIDEO_GENERATION_ENABLED=false
DOLA_RENDER_GATEWAY_ENABLED=false
VIDEO_GENERATION_CANARY_TENANT_IDS=
```

13. Install/update systemd unit.
14. `systemctl daemon-reload`.
15. Start only the Dola gateway.
16. Verify process user, bind, `/live`, `/ready`, and auth.
17. Execute CAM migration only when that mutation is part of the explicit authorization and rollback evidence has been recorded.
18. Restart/reload CAM units only if explicitly authorized and required.
19. Do not perform a real Dola generation.

## Runtime verification

Require:

```text
127.0.0.1:8100 listener
NO 0.0.0.0:8100
NO :::8100
wrong bearer rejected
right bearer accepted
browser executable can start under Dola user + Xvfb
CAM API healthy
existing video worker healthy
existing Search/API functions healthy
generation flags OFF
```

## Final report

```text
# DG-11 Deployment Result
- deployed CAM SHA:
- Dola runtime SHA:
- requirements/runtime hash:
- Patchright version:
- Chromium revision:
- system user:
- bind:
- /live:
- /ready:
- auth:
- CAM API:
- video worker:
- migration:
- generation flags OFF:
- accounts provisioned:
- real video generated: NO
- rollback target:

## Verdict
PASS / ROLLED BACK / BLOCKED

## DG-12 Readiness
READY / BLOCKED
```

STOP.

---

# 56. Account provisioning on VPS

Requires separate operator authorization and credentials supplied out of band.

Do not place credentials in Codex prompts.

Preferred secure behavior:

```bash
sudo -u dola-render-gateway \
  /var/lib/dola-render-gateway/current-runtime/bin/python \
  /opt/dola-render-gateway/current/manage_account.py add account-01
```

The program prompts securely.

If visual human intervention is needed:

```text
temporary VPS display/VNC bound to localhost
SSH tunnel from operator
perform human verification
stop/remove temporary remote-display service
```

Never expose VNC/noVNC publicly.

Do not copy a personal Windows Chrome profile to the VPS.

---

# 57. DG-12 — Explicit first production canary

Requires separate explicit canary authorization.

Example:

```text
Authorize DG-12 for tenant <approved tenant> with one 10-second generation.
```

Do not infer this from DG-11.

## Preconditions

All required:

```text
DG-05M PASS
DG-10 READY
DG-11 PASS
legal/terms gate resolved
at least one Dola VPS account verified
gateway loopback-only
internal auth verified
CAM migration correct
rollback target recorded
DOLA_MAX_CONCURRENCY=1
one tenant explicitly approved
```

## Enablement order

Start from all generation flags false.

Only when authorized:

```text
DOLA_RENDER_GATEWAY_ENABLED=true
VIDEO_GENERATION_ENABLED=true
VIDEO_GENERATION_CANARY_TENANT_IDS=<approved tenant>
```

Do not broaden the allowlist.

## First canary

Prefer one bounded request:

```text
Seedance 2.5
10 seconds
zero or one small reference
one request
```

Observe:

```text
CAM run state
CAM job state
Dola task state
number of provider tasks
idempotency behavior
browser process
RSS/CPU
available RAM
load
provider elapsed time
artifact download
CAM storage
output asset
risk/quota state
logs
```

## Stop conditions

Disable new generation immediately if any occurs:

```text
tenant leak
duplicate provider task
auth bypass
public Dola bind
browser OOM
CAM API degradation
video-worker queue runaway
Elasticsearch/system memory pressure
duplicate output asset
risk-control loop
profile corruption
rollback unavailable
```

Preserve forensic state. Do not mass-delete task DB/profile state.

## Final report

```text
# DG-12 Canary Result
- tenant:
- model:
- duration:
- reference count:
- CAM run:
- CAM job:
- provider task count:
- duplicate submit: NO
- output asset:
- elapsed:
- peak gateway/Chromium RSS:
- peak CPU:
- available RAM:
- load:
- risk/quota:
- errors:
- rollback:
- production flags after canary:

## Verdict
PASS / PASS WITH CHANGES / ROLLED BACK / BLOCKED
```

STOP.

---

# 58. Rollback design

## Source rollback

Before activation, source is default-off. Revert source commit(s) if necessary.

Do not use Alembic downgrade automatically. Additive unused tables may remain until separately reviewed.

## Gateway runtime rollback

Before runtime changes record:

```text
active release symlink
active runtime/venv hash
tasks.sqlite3 backup
pool_usage.sqlite3 backup
```

Do not copy an actively-written Chromium profile as a "consistent backup" unless the service/browser is stopped.

Rollback sequence:

```text
disable new CAM generation
stop gateway
switch Dola current symlink to previous release
switch runtime pointer if required
start gateway
verify /live
verify /ready
verify loopback
```

Design Dola SQLite schema evolution to be backward-compatible where practical.

## Canary rollback

First:

```text
VIDEO_GENERATION_ENABLED=false
```

This stops new requests.

Do not blindly delete already-submitted tasks. Decide whether to drain, ingest, or cancel based on incident type.

---

# 59. Backup policy

Execution databases:

```text
tasks.sqlite3
pool_usage.sqlite3
```

should be backed up before gateway schema/runtime upgrade.

Browser profiles contain operational session credentials. Treat them as secrets.

Never:

```text
commit profiles
upload profiles as normal CAM assets
put profiles in application logs
store profiles in public backup locations
```

---

# 60. Security checklist

Before source integration:

```text
[ ] upstream SHA pinned
[ ] no runtime secrets vendored
[ ] no profiles/DBs vendored
[ ] license/terms warning documented
```

Before gateway deployment:

```text
[ ] dedicated system user
[ ] loopback bind
[ ] fail-closed bearer
[ ] persistent profiles external to releases
[ ] Dola-only venv
[ ] concurrency=1
[ ] proxy explicitly configured
[ ] no public Nginx route
[ ] Xvfb configured
[ ] no root Chromium
[ ] no --no-sandbox shortcut
```

Before canary:

```text
[ ] idempotency tested
[ ] restart recovery tested
[ ] private references tested
[ ] authenticated output tested
[ ] tenant isolation tested
[ ] Viewer rules tested
[ ] output dedup tested
[ ] cancellation documented
[ ] VPS resources measured
[ ] account verified
[ ] rollback target known
[ ] explicit tenant approved
```

---

# 61. Data-flow privacy

CAM may receive:

```text
prompt
reference source asset IDs
model
ratio
duration
client_request_id
```

CAM worker may send to the Dola gateway:

```text
prompt
reference image bytes
model
ratio
duration
idempotency key
internal bearer
```

Never send to Dola gateway:

```text
CAM browser auth cookie
CAM OAuth refresh token
Google Drive source token
database credential
unrelated tenant secrets
```

The gateway never returns browser cookies/session credentials to CAM.

---

# 62. Test taxonomy

## Gateway unit

```text
config
internal auth
idempotency
TaskStore migration
reference validation
artifact path safety
recovery selection
error mapping
```

## Gateway integration with fake BrowserPool

```text
submit
duplicate submit
poll
complete
content
failed
cancel
DB reopen/restart
rate/quota mapping
```

No real Dola.

## CAM API

```text
capabilities
create
client idempotency
tenant isolation
Viewer/source scope
get
cancel
video endpoint
```

## CAM worker

```text
submit
lost-response retry
poll/defer
completion
storage retry
restart
cancel
429
503
401
provider failure
```

## Runtime isolation

```text
video role owns video_generate
image role does not
disabled startup
no Patchright import in CAM
```

## Frontend

```text
capability
submit
retry
poll
cancel
completed video
error
```

## Deployment

```text
systemd syntax
loopback
state paths
no Nginx exposure
no secrets
default flags off
```

---

# 63. Do not couple this to Visual Search

Dola work must not alter:

```text
SigLIP
Visual Search ranking
Visual Search pagination
Visual Search tenant policy
Search V3 semantics
```

If shared worker/API/bootstrap files change, run appropriate regressions.

---

# 64. Source reconnaissance lists

Before CAM work inspect current:

```text
apps/api/app/core/config.py
apps/api/app/main.py
apps/api/app/domain/processing/types.py
apps/api/app/domain/processing/handlers.py
apps/api/app/modules/processing/bootstrap.py
apps/api/app/modules/processing/worker_roles.py
apps/api/app/modules/image_generation/
apps/api/app/modules/storage/
apps/api/app/modules/assets/
deploy/production.env.example
deploy/systemd/creative-asset-manager-video-worker.service
apps/api/alembic.ini
database/migrations/env.py
database/migrations/versions/
```

Before Dola work inspect current:

```text
server.py
config.py
store.py
browser.py
browser_pool.py
video_worker_ui.py
video_worker.py
dola_client.py
media.py
add_account.py
requirements.txt
README.md
extensions/
```

Current source always wins over stale assumptions.

---

# 65. Main source-integration gate — DG-05M

The backend through DG-05 may enter `main` only when:

```text
[ ] DG-03A PASS
[ ] DG-04A PASS
[ ] DG-05 PASS
[ ] latest origin/main reverified
[ ] Dola upstream reverified
[ ] current-main CI baseline captured/classified
[ ] deterministic current-main CI defects repaired
[ ] PostgreSQL 0066 downgrade psycopg-safe; no stamp
[ ] test metadata bootstrap complete with production FKs unchanged
[ ] frontend managed-storage cleanup-policy contract green
[ ] no new integration failure signature introduced
[ ] storage/config overlap reconciled
[ ] processing-policy/concurrency overlap reconciled
[ ] final generated video has intentional durable storage semantics
[ ] final video cannot be accidentally swept as temporary analysis staging
[ ] bounded staging quota cannot be stranded by final outputs
[ ] existing adaptive cleanup behavior remains green
[ ] stale concurrency counters clamp at zero
[ ] video_generate cannot double-release concurrency accounting
[ ] tenant/category/provider active counters remain non-negative
[ ] one expected Alembic head
[ ] 0066 -> 0067 -> 0066 -> 0067 isolated migration proof
[ ] video-generation critical tests green
[ ] current-main managed-storage/cleanup regressions green
[ ] worker/runtime/API startup regressions green
[ ] feature flags default OFF
[ ] canary allowlist empty
[ ] no secret/profile/db/runtime artifact committed
[ ] Dola auth fail-closed
[ ] stable gateway idempotency proven
[ ] private references and authenticated content proven
[ ] completed result stored as CAM durable asset
[ ] no Patchright/browser dependency imported by CAM API/worker
[ ] no force push
[ ] no production action performed
```

Current `main` was unprotected at the v1.6 audit. Therefore non-force integration discipline and pre-push verification are mandatory process gates.

Frontend and deployment runtime artifacts remain subsequent tasks DG-06/DG-07. Frontend `dist/**`, however, is a **DG-06 source/build artifact requirement** because current CI enforces source/dist equality.

---

# 66. Production-deployment gate

No production mutation until:

```text
[ ] DG-05M PASS and source present in main
[ ] post-integration main CI has no unexplained/new failures
[ ] DG-06 frontend PASS with committed dist synchronized
[ ] DG-07 deployment artifacts/runbook PASS
[ ] DG-08 release review PASS
[ ] legal/terms gate resolved for intended use
[ ] DG-10 read-only VPS audit complete
[ ] resource headroom acceptable
[ ] rollback target recorded
[ ] explicit production authorization received
```

A source merge into `main` is not deployment. A green source CI on `main` is necessary but not sufficient for production deployment.

---

# 67. Canary gate

No real provider generation until:

```text
[ ] DG-11 PASS
[ ] account verified on VPS
[ ] concurrency=1
[ ] one tenant explicitly approved
[ ] stop conditions ready
[ ] explicit canary authorization received
```

---

# 68. Secrets inventory

Expected sensitive material includes:

```text
DOLA_RENDER_GATEWAY_INTERNAL_KEY
Dola/Google provisioning credentials
Dola browser profiles/cookies
existing CAM DB/OAuth/storage secrets
```

Never print complete env files in Codex reports.

Never include secret values in commit messages, diffs, screenshots, logs, or final reports.

---

# 69. Upstream update procedure

For any future Dola update:

1. resolve current imported SHA;
2. resolve candidate upstream SHA;
3. inspect the commit diff;
4. inspect API/auth/store/browser/account/extension/dependency changes;
5. import the candidate snapshot;
6. reapply only documented local hardening;
7. update `UPSTREAM.md`;
8. rerun gateway tests;
9. rerun CAM worker/API regressions;
10. do not auto-deploy.

Never overwrite local hardening blindly.

---

# 70. Incident examples

## Gateway unavailable

```text
CAM API remains online
new generation may fail/defer according to policy
disable generation flag if sustained
repair gateway
resume provider tasks
```

## Profile invalid

```text
disable that account from scheduling
preserve profile
reverify/reprovision manually
do not delete immediately
```

## Duplicate provider task

Treat as P1:

```text
disable VIDEO_GENERATION_ENABLED
preserve CAM/Dola DB state
map all related tasks
investigate idempotency
do not mass-delete
```

## Memory pressure

```text
disable new generation
keep concurrency=1
capture RSS/load
stabilize active job according to severity
do not increase concurrency or install random swap as first response
```

---

# 71. Desired user experience

The CAM user can:

```text
open Generate Video
enter prompt
choose Seedance version
choose ratio
choose duration
select optional CAM reference assets
submit
leave/reopen page
see durable state
cancel when possible
play/download completed video from CAM
```

The user never needs to know the gateway port, provider task URL, account name, profile, cookie, filesystem, or secret.

---

# 72. Desired operator experience

The operator can:

```text
deploy/rollback Dola independently
verify liveness/readiness
verify/list account profiles safely
provision a profile securely
disable an unhealthy account
inspect aggregate gateway state
disable video generation globally
change canary tenant policy
```

without exposing the Dola admin dashboard publicly.

---

# 73. Definition of completion

Backend source integration milestone:

```text
DG-00..DG-05 PASS
DG-05M PASS
latest-main storage semantics reconciled
no new integration CI failure signature
```

means the verified backend is in `main` with generation flags OFF.

Product/source readiness milestone:

```text
DG-06 PASS including synchronized client dist
DG-07 PASS
DG-08 PASS
```

means frontend, deployment artifacts, and release review are complete on `main`.

Runtime deployment:

```text
DG-10 READY
DG-11 PASS
```

means the gateway exists and is healthy on the VPS while generation can still remain OFF.

First production canary:

```text
DG-12 PASS
```

means one explicitly authorized real generation succeeded under observation.

---

# 74. Current next action

Run only:

```text
DG-05M-CI -> DG-05M-S -> DG-05M-P -> DG-05M-M -> DG-05M-I

Repair/reconcile latest-main CI + storage + processing concurrency semantics,
then integrate DG-00..DG-05 into latest origin/main with generation flags OFF.
```

Current latest verified main for the starting comparison:

```text
3a3f60f267b433742adec5a38f64f1d274843253
fix(processing): recover stale concurrency counters
```

Current latest verified Dola upstream:

```text
ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4
```

Current GitHub facts at v1.6 audit time:

```text
main branch protected: NO
remote feat/dola-render-gateway-integration: stale / behind main
main Dola/video-generation module: absent
main migration 0067_video_generation_domain: absent
latest main CI run: 34670741926 = RED
deterministic blockers: 0066 psycopg downgrade; oauth_connections test metadata;
                        frontend cleanup-policy contract
durable pipeline E2E: PASS
Windows Electron package: PASS
```

Re-fetch everything before execution. Do not begin DG-06 automatically.

---

# 75. Short Codex command to start

```text
Read docs/cam-dola-render-gateway-master-implementation-guide.md completely.

Execute DG-05M only, in this order:
DG-05M-CI -> DG-05M-S -> DG-05M-P -> DG-05M-M -> DG-05M-I.

Use the actual local verified DG commit chain through:
adb91f3d48ce3a3fffd334190dad89a64e664516

Do not assume the remote feat/dola-render-gateway-integration branch contains those commits.

Fetch latest origin/main. The latest audit when this guide was written was:
3a3f60f267b433742adec5a38f64f1d274843253

First repair/revalidate current-main CI hygiene. The v1.6 audit found:
- PG12/PG16 downgrade failure in 0066_video_gemini_backups caused by raw `%`
  placeholder parsing under psycopg 3; preserve downgrade guard, no stamp.
- Elasticsearch and API/unit selected-model metadata omit oauth_connections;
  repair test model registration, never weaken production tenant FKs.
- SettingsStorageCleanupPolicyPanel managed-storage gating test mismatch.
- Durable pipeline E2E is green and must stay green.

Then reconcile latest managed-storage cleanup/capacity semantics. Prove generated MP4s
are durable final outputs, cannot be swept as temporary analysis staging, and cannot
strand staging quota or double-release capacity reservations.

Then reconcile current-main processing policy from 3a3f60f. Prove video_generate
deferred polling, lease expiry, cancellation/failure and recovery cannot double-release
concurrency slots, concurrency_accounted clears exactly once, and tenant/category/provider
counters clamp at zero and never become negative. Policy tests must not call Dola.

Only after CI/storage/processing gates pass, create a clean temporary integration worktree
from current origin/main, cherry-pick/reconcile the verified DG series, require one Alembic
head, prove 0066 -> 0067 -> 0066 -> 0067 on isolated acceptance DBs, rerun DG-05 and
mainline regression gates, verify video-generation flags remain OFF, fetch origin/main
again, and integrate/push main only if final gates pass.

Never force-push.
Do not mutate a real dev/prod DB for acceptance.
Do not perform production actions.
Do not start DG-06 automatically.

After DG-05M PASS, subsequent source work is performed directly on clean, up-to-date main.
DG-06 must keep tracked apps/client/dist synchronized with frontend source because CI
enforces build/dist equality.
```

---

# 76. Non-negotiable summary

```text
CAM PostgreSQL = business authority
Dola SQLite = local execution/recovery only
CAM Asset Registry + Managed Storage = final generated media authority

CAM Video Worker = orchestration
Dola Gateway = isolated browser execution

gateway = loopback only
auth = dedicated fail-closed bearer
submit = stable idempotency key
references = private ordered multipart
output = authenticated stream -> bounded CAM local stage -> Asset -> durable Managed Storage

submission_unknown = never blind resubmit
storage/content retry = never provider resubmit
stored output recovery = no Dola GET and no second storage upload

final generated MP4 != disposable analysis staging
latest-main cleanup/capacity semantics must be reconciled before merge
latest-main processing counters = clamp stale release at zero; never double-release
video_generate concurrency_accounted = release exactly once

Patchright/Chromium = separate process + venv + system user
concurrency V1 = 1

current main Dola integration = NOT MERGED YET
current main CI at v1.6 audit = RED, deterministic repair items identified
DG-05M = CI/storage/processing reconciliation + one-time integration into latest main
DG-06 onward = direct-on-main only after DG-05M PASS
DG-06 frontend source must keep tracked dist synchronized
one focused DG gate at a time

main currently unprotected => never force-push; process gates are mandatory
source in main != production deployed
production deployed != generation enabled
generation enabled != broad rollout

no production mutation without explicit task-specific authorization
```

---

# 77. Revision changelog

```text
v1.2
- established the original isolated feature-branch implementation/deployment taskbook

v1.3
- incorporated completed backend implementation evidence through DG-05

v1.4
- revalidated main at b8695c...
- added Managed Storage cleanup/capacity reconciliation
- corrected frontend tracked-dist policy
- introduced DG-05M as the pre-DG-06 mainline integration gate

v1.5
- revalidated main at 3a3f60f267b433742adec5a38f64f1d274843253
- recorded new stale concurrency-counter recovery semantics
- reclassified current CI run 34670741926 from fresh logs
- identified 0066 psycopg raw-percent downgrade defect
- grouped Elasticsearch + API/unit oauth_connections errors as test metadata-registration defects
- recorded frontend SettingsStorageCleanupPolicyPanel contract failure
- added DG-05M-P processing concurrency-accounting reconciliation
- strengthened DG-05M expected exit to deterministic green CI before DG-06

v1.6
- revalidated CAM main remains 3a3f60f267b433742adec5a38f64f1d274843253 with no new source drift
- revalidated Dola upstream remains ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4
- corrected CI run 34670741926 Windows Electron package from SKIPPED to PASS
- confirmed Durable pipeline end-to-end remains PASS while five deterministic jobs remain red
- inventoried all five visible remote Dola integration refs and confirmed none contains DG-00..DG-05
- preserved DG-05M as the next active gate with no architecture/task-order change
```

---

**End of master guide.**

## DG-10B — Heavy video mutual exclusion

CAM enforces a database-backed, capacity-one, **global** resource lane named
`heavy_video`. It is not a worker-concurrency setting, advisory lock, or
in-memory mutex. A pre-seeded `processing_resource_leases` row is locked in the
same PostgreSQL transaction that changes its owner, so all tenants and worker
processes compete fairly for one durable slot.

`video_analyze` acquires the lane before proxy materialization, FFmpeg, or
Gemini work. If busy, the ProcessingJob is deferred without starting those
expensive operations. An analysis lease is released when its job completes,
fails permanently, or is cancelled. Recovery may clear only an analysis lease
whose authoritative ProcessingJob is terminal or has an expired worker lease.

`video_generate` acquires the same lane before reference preparation, gateway
submit, or gateway polling. It remains held through preparing, submitted,
running, deferred polling, `submission_unknown`, and storing. It is released
only after the authoritative CAM generation run is `completed`, `failed`, or
`cancelled`; a deferred poll never releases it. Thus a lost/restarted CAM worker
cannot permit an analysis to overlap unresolved provider state.

`video_search_index` does not use this lane. Existing tenant/provider claim
limits and `concurrency_accounted` behavior are unchanged; the lane is an
additional host-global safety boundary. The CAM Video Worker remains the single
service that may host either mode, but never both heavy modes concurrently.
