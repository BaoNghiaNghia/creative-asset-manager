# Inventory Automation — Isolated Codex Implementation Plan

> Repository: `BaoNghiaNghia/creative-asset-manager`  
> Branch: `feature/google-drive-explorer-mvp`  
> Source baseline reviewed: `eac0d8a4c2b19eb1e34f624a73cf72d6f40ddbf8`  
> Architecture revision date: 2026-08-09  
> Status: **THIS VERSION SUPERSEDES THE PREVIOUS SHARED-PIPELINE INVENTORY PLAN**  
> Primary requirement: the new Inventory Automation flow and the existing Creative Asset flow must be operationally isolated so a failure, backlog, retry storm, schema change, pause, AI error, or business rule in one flow does not affect the other.

---

# 0. Architecture decision

Inventory is **not** an extension of the existing creative-asset processing pipeline.

Inventory is a separate application domain and separate asynchronous pipeline living in the same repository/application platform.

The two flows may reuse only stable low-level platform infrastructure where reuse does not create runtime coupling.

Target shape:

```text
                         SHARED PLATFORM LAYER
              ┌────────────────────────────────────┐
              │ FastAPI app shell                  │
              │ Authentication / tenant identity   │
              │ PostgreSQL engine                  │
              │ Google OAuth connection storage    │
              │ GoogleDriveClient transport        │
              │ common image utility, if stateless │
              └──────────────┬─────────────────────┘
                             │
               ┌─────────────┴─────────────┐
               │                           │
               ▼                           ▼
     CREATIVE ASSET FLOW             INVENTORY FLOW
     existing code                   new isolated code

     source_sync                     inventory_drive_poll
          ↓                               ↓
     SourceAssetModel                InventorySourceFile
          ↓                               ↓
     processing_jobs                 inventory_jobs
          ↓                               ↓
     AssetModel                      InventorySubmission/Page
          ↓                               ↓
     asset_store                     inventory image storage
          ↓                               ↓
     AssetAiAnalysisModel            InventoryAiAnalysis
          ↓                               ↓
     search projection               inventory normalize/validate
          ↓                               ↓
     Elasticsearch                   review / transactions
                                          ↓
                                     daily finalize
                                          ↓
                                     Excel export
```

**Hard rule:** Inventory data must never enter the existing generic `source_sync → source_asset_download → AssetModel → asset_analyze → search_projection_build → asset_index` chain.

**Hard rule:** Existing creative-asset modules must not import Inventory modules.

---

# 1. How Codex must use this document

This file is the primary technical implementation plan for Inventory Automation.

Source-of-truth precedence:

1. current source code on the target branch;
2. this document;
3. `docs/google-drive-inventory-automation.md` for business rules and operational workflow;
4. older inventory readiness/planning documents for historical context only.

Codex execution rules:

- Re-read current source before every phase.
- Implement **one phase per task/PR** unless explicitly instructed otherwise.
- Stop at the phase boundary.
- Do not opportunistically refactor the existing creative pipeline.
- Do not put Inventory job types into generic `processing_jobs`.
- Do not put Inventory extraction results into `AssetAiAnalysisModel`.
- Do not create Inventory `SourceAssetModel` or `AssetModel` records.
- Do not index Inventory documents into the existing creative Elasticsearch index.
- Do not use generic `AssetProcessingStatusService` for Inventory state.
- Do not use generic `MetadataProfileModel` as the authoritative Inventory extraction profile.
- Do not change the meaning of existing creative feature flags or tenant processing policies.
- Every Inventory feature flag must default to disabled.
- Every phase must include tests proving that existing creative behavior remains unchanged.
- Relevant CI must be green before moving to the next phase.

---

# 2. Isolation level

The desired isolation is **pipeline isolation inside one product**, not a completely separate product.

## 2.1. Allowed shared infrastructure

Inventory may reuse:

- FastAPI application process and dependency injection;
- existing authentication and `CurrentPrincipal` tenant identity;
- PostgreSQL server/SQLAlchemy engine;
- Alembic migration mechanism;
- persisted Google OAuth connection records;
- token refresh/access-token resolution helpers;
- `GoogleDriveClient` network transport methods;
- stateless image normalization utilities when they do not write generic asset state;
- common logging/tracing primitives;
- common UI shell/layout components.

These are infrastructure dependencies, not business-pipeline dependencies.

## 2.2. Must be separate

Inventory must have its own:

- configuration and feature flags;
- Drive Inbox binding;
- Drive polling cursor/state;
- source-file registry;
- download/submission records;
- durable job table;
- job repository;
- job claimer;
- worker runtime;
- retry/backoff rules;
- pause/resume state;
- concurrency limits;
- AI analysis records;
- extraction schema/version;
- AI rate-limit/budget configuration;
- review queue;
- business transactions;
- daily-run scheduler;
- daily finalization;
- Excel exporter;
- status projection;
- API routes;
- React routes/pages;
- metrics namespace;
- operational dashboard.

---

# 3. Isolation guarantees

The implementation is accepted only when these statements are true.

## Guarantee A — Inventory failure cannot fail creative jobs

Examples:

```text
Inventory AI 429
Inventory OCR malformed JSON
Inventory Excel export exception
Inventory Drive archive 403
Inventory worker crash
Inventory queue backlog
```

None of the above may alter:

```text
processing_jobs
AssetPipelineModel
AssetAiAnalysisModel
creative tenant processing pause state
creative provider runtime stop state
creative search projection/index state
```

## Guarantee B — Creative pause cannot pause Inventory

Pausing generic processing must not stop `inventory_worker`.

Inventory has its own control row, for example:

```text
inventory_processing_controls
```

with its own:

```text
enabled
paused
max_active_jobs
max_ai_jobs
```

## Guarantee C — Inventory backlog cannot consume generic worker slots

Inventory jobs never appear in `processing_jobs`, so generic `TenantAwareJobClaimer` does not see them.

Do not add Inventory job names to:

```text
JobType
STAGE_POLICY
AI_JOB_TYPES
SOURCE_JOB_TYPES
STORAGE_JOB_TYPES
```

of the existing processing pipeline.

## Guarantee D — Inventory AI cannot satisfy creative AI, and vice versa

Inventory extraction uses `InventoryAiAnalysisModel`.

Creative analysis continues to use `AssetAiAnalysisModel`.

There must be no cross-reuse based only on content hash.

## Guarantee E — Inventory files never reach creative search

An image submitted to the Inventory Inbox must not create:

```text
SourceAssetModel
AssetModel
AssetSourceLinkModel
AssetPipelineModel
AssetAiAnalysisModel
SearchOperationItemModel
Elasticsearch creative document
```

## Guarantee F — Inventory UI state is independent

Creative Explorer processing status remains generated from current creative models.

Inventory status is generated from Inventory tables only.

---

# 4. External-provider isolation

Application-level isolation does not automatically isolate external quota.

If Creative and Inventory use the same Gemini project or the same OpenAI API key/project, provider quota, billing caps, or provider outages can still couple the two flows externally.

For strong production isolation, use separate provider credentials/configuration:

```text
CREATIVE AI
  GEMINI_API_KEY / project A
  OPENAI_API_KEY / project A

INVENTORY AI
  INVENTORY_GEMINI_API_KEY / project B
  INVENTORY_OPENAI_API_KEY / project B
```

MVP may initially use one provider if necessary, but Codex must preserve separate Inventory rate-limit/budget state and configuration so credentials can be split without redesigning the domain.

Recommended production rule:

> Separate AI credentials/project for Inventory before full automation rollout.

---

# 5. Google Drive boundary

Inventory may reuse the existing tenant Google Drive OAuth connection, because that is an infrastructure connection rather than a processing pipeline.

Current source already supports a write-scoped Drive connection and token refresh.

Inventory must not register its Inbox into the generic creative source-sync pipeline.

## 5.1. Folder structure

```text
KIEM_KHO_TU_DONG/
├── 00_INBOX_NHAN_VIEN/
├── 01_DA_XU_LY/
│   └── YYYY-MM-DD/
├── 02_CAN_CHUP_LAI/
│   └── YYYY-MM-DD/
├── 03_FILE_EXCEL_CHINH/
├── 04_BAN_SAO_HANG_NGAY/
└── 05_LUU_TRU_ANH_CU/
```

## 5.2. Inventory Drive poller

Create a separate service:

```text
InventoryDrivePoller
```

It should call the low-level Drive provider/client using the configured tenant OAuth connection.

MVP behavior:

1. list files directly inside `00_INBOX_NHAN_VIEN`;
2. ignore folders;
3. accept only supported image MIME types;
4. compare provider identity/version against `inventory_source_files`;
5. create an Inventory source record for new/changed submissions;
6. enqueue an Inventory download job in `inventory_jobs`;
7. never call generic source-sync services.

A simple bounded folder listing every 1–5 minutes is preferred for MVP because it is easy to reason about and remains isolated from the account-wide creative Drive changes cursor.

Do not reuse the creative source cursor.

Future optimization may use a dedicated Drive Changes cursor stored in Inventory tables, but it still must remain separate.

## 5.3. Source identity/version

Recommended idempotency identity:

```text
tenant_id
external_source_id
drive_file_id
drive_modified_time
```

After download also persist:

```text
content_sha256
```

This supports both provider-version idempotency and duplicate-content detection.

---

# 6. New Inventory module structure

Create:

```text
apps/api/app/modules/inventory/
├── __init__.py
├── config.py
├── permissions.py
├── model.py
├── schema.py
├── repository.py
├── router.py
│
├── drive/
│   ├── poller.py
│   ├── source_repository.py
│   ├── downloader.py
│   └── archive.py
│
├── jobs/
│   ├── model.py
│   ├── repository.py
│   ├── claimer.py
│   ├── registry.py
│   ├── runtime.py
│   ├── handlers.py
│   └── scheduler.py
│
├── ai/
│   ├── model.py
│   ├── repository.py
│   ├── gateway.py
│   ├── schema.py
│   ├── prompt.py
│   ├── rate_limit.py
│   └── budget.py
│
├── documents/
│   ├── service.py
│   ├── normalization.py
│   └── validation.py
│
├── review/
│   └── service.py
│
├── transactions/
│   └── service.py
│
├── daily/
│   ├── service.py
│   ├── scheduler.py
│   └── report.py
│
└── export/
    ├── excel.py
    └── drive.py
```

Tests should mirror the same domain boundaries under:

```text
apps/api/tests/modules/inventory/
```

---

# 7. Inventory database model

All Inventory tables are tenant-scoped.

Use a clear `inventory_` prefix so queries, migrations, dashboards and support tools cannot confuse them with creative tables.

Minimum tables:

```text
inventory_settings
inventory_processing_controls
inventory_source_files
inventory_jobs
inventory_locations
inventory_items
inventory_item_aliases
inventory_documents
inventory_document_pages
inventory_ai_analyses
inventory_lines
inventory_reviews
inventory_transactions
inventory_daily_runs
inventory_exports
```

## 7.1. `inventory_settings`

Suggested fields:

```text
id
tenant_id
enabled
external_source_id
inbox_folder_id
processed_folder_id
reupload_folder_id
excel_folder_id
backup_folder_id
excel_template_file_id
timezone
auto_approve_confidence
review_confidence
drive_poll_interval_seconds
archive_enabled
excel_export_enabled
created_at
updated_at
```

One active row per tenant for MVP.

Tokens are never stored here.

## 7.2. `inventory_processing_controls`

Suggested fields:

```text
tenant_id
enabled
paused
max_active_jobs
max_ai_jobs
updated_at
updated_by
```

Do not reuse `TenantProcessingPolicyModel`.

## 7.3. `inventory_source_files`

Suggested fields:

```text
id
tenant_id
external_source_id
drive_file_id
filename
mime_type
drive_modified_time
drive_size
content_sha256
status
last_seen_at
downloaded_at
created_at
updated_at
```

Unique provider-version key:

```text
tenant_id + external_source_id + drive_file_id + drive_modified_time
```

## 7.4. `inventory_jobs`

This is a separate durable queue.

Suggested fields:

```text
id
tenant_id
job_type
entity_type
entity_id
status
priority
payload_json
idempotency_key
attempt_count
max_attempts
next_attempt_at
claimed_by
claimed_at
lease_expires_at
cancellation_requested
last_error_code
last_error_message
created_at
updated_at
completed_at
```

Required queue semantics:

- idempotent enqueue;
- `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL;
- lease expiration/recovery;
- retry with bounded backoff;
- terminal non-retryable failure;
- cancellation;
- separate Inventory concurrency control.

Do not insert these jobs into generic `processing_jobs`.

## 7.5. Inventory business tables

Use dedicated models for:

```text
inventory_locations
inventory_items
inventory_item_aliases
inventory_documents
inventory_document_pages
inventory_lines
inventory_reviews
inventory_transactions
inventory_daily_runs
inventory_exports
```

Inventory page identity should reference `inventory_source_files.id`, not `SourceAssetModel.id`.

---

# 8. Inventory job types

Recommended MVP Inventory job registry:

```text
inventory_file_download
inventory_document_prepare
inventory_document_analyze
inventory_document_normalize
inventory_document_validate
inventory_document_archive
inventory_daily_finalize
inventory_excel_export
inventory_daily_report
```

These strings exist only in the Inventory registry.

They must not be added to generic processing domain types.

Typical chain:

```text
inventory_file_download
        ↓
inventory_document_prepare
        ↓
inventory_document_analyze
        ↓
inventory_document_normalize
        ↓
inventory_document_validate
        ↓
     ┌───────────────┐
     │               │
     ▼               ▼
 approved        needs_review
     │
     ▼
inventory_document_archive
```

Daily chain:

```text
inventory_daily_finalize
        ↓
inventory_excel_export
        ↓
inventory_daily_report
```

---

# 9. Separate Inventory worker

Create a separate worker entrypoint/process, for example:

```text
apps/inventory_worker/
```

or an equivalent explicit runtime command under the API package.

Recommended deployment processes:

```text
api
creative-worker
inventory-worker
inventory-scheduler
```

The Inventory worker claims **only `inventory_jobs`**.

The Creative worker keeps its current behavior and claims **only `processing_jobs`**.

No shared in-memory registry.

No shared queue polling loop.

No shared tenant concurrency counters.

This is the core runtime isolation boundary.

---

# 10. Separate Inventory scheduler

Inventory scheduler handles only:

```text
Drive polling
daily completeness checks
17:00 finalization
Excel/report scheduling
retry maintenance for inventory_jobs
```

It does not invoke the generic source-sync scheduler.

Recommended business timezone:

```text
Asia/Ho_Chi_Minh
```

Suggested schedule:

```text
14:00 expected staff submission window begins
16:30 check missing required documents
16:50 final pre-close scan
17:00 finalize eligible business day
17:10 export/report
```

Exact times belong to Inventory settings and must not alter creative schedulers.

---

# 11. Image handling

Inventory should not create an `AssetModel` merely to use image preprocessing.

Preferred implementation:

1. download original bytes into Inventory-controlled storage/temp file;
2. calculate SHA-256;
3. call a **stateless/common image preparation utility**;
4. store prepared analysis image identity in Inventory tables/storage;
5. pass prepared bytes to Inventory AI gateway.

If existing `AnalysisImagePreparer` is tightly coupled to generic asset-storage records, extract only the stateless image transform into a common utility first.

Do not make Inventory depend on `AssetStorageObjectModel`.

Inventory storage namespace example:

```text
inventory/
  {tenant_id}/
    source/
    prepared/
    exports/
```

Creative storage paths remain unchanged.

---

# 12. Separate Inventory AI domain

Inventory AI must not create `AssetAiAnalysisModel`.

Create:

```text
InventoryAiAnalysisModel
```

Suggested fields:

```text
id
tenant_id
document_page_id
content_sha256
extraction_profile
extraction_profile_version
prompt_version
schema_version
provider
model
status
attempt_count
raw_response_json
extracted_json
validation_errors_json
usage_json
estimated_cost
provider_request_id
last_error_code
last_error_message
created_at
started_at
completed_at
updated_at
```

Normal-run uniqueness should include at least:

```text
tenant_id
content_sha256
extraction_profile_version
prompt_version
schema_version
provider
model
```

## 12.1. Prompt/profile

Inventory extraction profile is code/config owned by the Inventory domain, for example:

```text
inventory-stock-sheet-v1
```

Do not select the newest generic metadata profile.

Do not allow creative profile activation/deactivation to change Inventory extraction behavior.

## 12.2. AI gateway

Create an `InventoryAiGateway` with explicit provider configuration.

It may reuse a low-level provider transport abstraction only if that abstraction can be instantiated without writing creative analysis/governance state.

Otherwise create a thin Inventory-specific provider adapter.

The important boundary is data/control isolation, not maximum code reuse.

## 12.3. Inventory AI controls

Inventory must have independent:

```text
provider enabled/disabled
model allowlist
RPM/start interval
concurrency
per-run limit
daily budget
monthly budget
emergency stop
```

Do not reuse creative provider pause/emergency-stop rows as Inventory control state.

---

# 13. Inventory extraction schema

AI output must be structured JSON and validated before any transaction is created.

Example shape:

```json
{
  "document_type": "stock_count",
  "business_date": "2026-08-09",
  "location": "PHONG_PHA_CHE",
  "page_number": 1,
  "page_count": 2,
  "lines": [
    {
      "raw_item_name": "TC OLONG",
      "whole_quantity": 2,
      "whole_unit": "goi",
      "fraction_quantity": 250,
      "fraction_unit": "g",
      "waste_quantity": 0,
      "waste_reason": null,
      "confidence": 0.97
    }
  ]
}
```

Never auto-invent unclear quantities, units, item mappings, or waste reasons.

---

# 14. Normalization, validation and review

These remain fully inside Inventory.

Normalization responsibilities:

- normalize Vietnamese whitespace/casing;
- alias matching;
- exact item master resolution;
- whole/fraction unit conversion;
- business-date normalization;
- location/document-type normalization.

Validation responsibilities:

- known item required;
- valid location;
- valid units;
- non-negative quantities;
- complete page set;
- duplicate submission detection;
- suspicious delta detection;
- waste quantity requires waste reason;
- transfer source/destination consistency;
- confidence thresholds.

Suggested outcomes:

```text
APPROVED
NEEDS_REVIEW
NEEDS_REUPLOAD
REJECTED
```

Unknown alias must become a review item, not a new master item.

---

# 15. Inventory transaction rules

Inventory transactions are append-oriented business records.

Example transfer:

```text
KHO_PHA_CHE → PHONG_PHA_CHE
```

creates linked transaction rows:

```text
transfer_out at KHO_PHA_CHE
transfer_in  at PHONG_PHA_CHE
```

Both rows share the same source document/transfer identity.

Waste records persist:

```text
item
quantity
unit/base quantity
reason
source document
source line
```

Usage calculation remains:

```text
Usage = Opening + Receipts + Transfers In
        - Transfers Out - Closing - Waste
```

Negative or implausible computed usage is a review/anomaly; never silently clamp to zero.

---

# 16. Separate Inventory API

Recommended prefix:

```text
/api/inventory
```

Suggested routes:

```text
GET    /api/inventory/settings
PUT    /api/inventory/settings
GET    /api/inventory/status/today
GET    /api/inventory/documents
GET    /api/inventory/documents/{id}
GET    /api/inventory/reviews
POST   /api/inventory/reviews/{id}/approve
POST   /api/inventory/reviews/{id}/correct
POST   /api/inventory/reviews/{id}/request-reupload
GET    /api/inventory/daily-runs/{date}
POST   /api/inventory/daily-runs/{date}/finalize
POST   /api/inventory/exports/{date}
GET    /api/inventory/jobs
POST   /api/inventory/jobs/{id}/retry
POST   /api/inventory/control/pause
POST   /api/inventory/control/resume
```

Inventory endpoints use Inventory permissions and Inventory models only.

Do not route through generic AI Operations endpoints.

---

# 17. Separate Inventory React area

Add a dedicated top-level navigation area, for example:

```text
/inventory
/inventory/inbox
/inventory/review
/inventory/daily
/inventory/reports
/inventory/settings
```

Recommended pages:

```text
InventoryDashboardPage
InventoryInboxPage
InventoryReviewPage
InventoryDailyRunPage
InventoryReportsPage
InventorySettingsPage
```

The existing Explorer remains a creative-asset experience.

Do not add Inventory status badges to creative Asset cards for MVP.

Do not reuse creative AI Operations screens as the Inventory operational dashboard.

Shared visual components are acceptable; shared state machines are not.

---

# 18. Excel boundary

PostgreSQL Inventory tables are source of truth.

Excel is output only.

Export flow:

```text
Inventory PostgreSQL snapshot
        ↓
openpyxl
        ↓
validate workbook invariants
        ↓
write new export file
        ↓
Google Drive inventory export folder
```

Do not use creative managed-storage records for Excel exports.

Do not write generated Excel files into generic asset ingestion folders.

Protected invariant:

> Sheet 4 `Báo cáo sử dụng NVL trong ca` must remain completely unchanged.

Before and after export, fingerprint at least:

- sheet name/order;
- dimensions;
- cell values/formulas;
- merged cells;
- row heights/column widths where practical;
- relevant styles where practical.

Export must fail closed if the protected sheet changes unexpectedly.

---

# 19. Metrics and logs

Use a separate namespace:

```text
inventory_drive_poll_*
inventory_job_*
inventory_ai_*
inventory_review_*
inventory_daily_*
inventory_export_*
```

Creative dashboards must not count Inventory jobs as creative jobs.

Inventory dashboard must not infer state from creative job tables.

Log every Inventory job with:

```text
tenant_id
inventory_job_id
job_type
entity_id
attempt
correlation_id
```

Never log OAuth access tokens or provider secrets.

---

# 20. Failure behavior

## Inventory Drive failure

```text
Inventory poll/download fails
→ retry inventory job/poll only
→ creative source sync continues normally
```

## Inventory AI failure

```text
Inventory provider/rate limit fails
→ defer/retry inventory AI job
→ no creative processing policy mutation
```

## Inventory Excel failure

```text
Excel export fails
→ business day remains finalized in PostgreSQL
→ export status failed
→ retry export only
```

## Inventory worker failure

```text
inventory-worker down
→ inventory_jobs accumulate
→ creative-worker continues processing_jobs
```

## Creative worker failure

```text
creative-worker down
→ creative processing_jobs accumulate
→ inventory-worker continues inventory_jobs
```

---

# 21. Required isolation tests

These tests are mandatory and are more important than code-reuse convenience.

## 21.1. Data isolation test

Submit an Inventory image and assert that no new rows are created in:

```text
source_assets
assets
asset_source_links
asset_pipelines
asset_ai_analyses
search operation/index tables
```

## 21.2. Queue isolation test

Create 1,000 pending `inventory_jobs` and prove the generic processing claimer still selects the same creative job it would have selected without the backlog.

## 21.3. Pause isolation test

```text
pause creative → inventory job still claimable
pause inventory → creative job still claimable
```

## 21.4. Failure isolation test

Force Inventory AI and Excel handlers to throw and confirm no creative model/control rows are changed.

## 21.5. Worker isolation test

Run only creative worker:

```text
processing_jobs drain
inventory_jobs unchanged
```

Run only inventory worker:

```text
inventory_jobs drain
processing_jobs unchanged
```

## 21.6. UI isolation test

Inventory API failure must not break Explorer navigation/rendering.

Creative API failure must not corrupt Inventory persisted state.

## 21.7. External quota test/configuration

When production credentials are split, assert Inventory provider factory uses only Inventory credentials and creative factory uses only creative credentials.

---

# 22. Implementation phases for Codex

# PHASE 0 — Restore green baseline

Goal: start from a known-good branch.

Current known baseline CI failures:

```text
modules.ai_governance.test_multi_provider_governance
.test_preclaim_honors_provider_mode_limit_and_runtime_stop

modules.ai_operations.test_api
.test_summary_uses_canonical_current_job_states_and_latest_replacement
```

Requirements:

- fix only current regressions;
- no Inventory feature code in Phase 0;
- run relevant API/unit tests;
- preserve currently passing PostgreSQL, Elasticsearch, frontend and pipeline E2E checks.

**Stop after Phase 0.**

---

# PHASE 1 — Isolation scaffolding

Goal: create explicit architectural boundaries before business logic.

Implement:

- `modules/inventory/` package;
- Inventory config with default-off flags;
- Inventory permissions;
- Inventory router shell;
- Inventory processing-control model;
- Inventory job model/repository/claimer skeleton;
- inventory worker bootstrap with no business handlers yet;
- tests proving generic `processing_jobs` and `TenantAwareJobClaimer` are untouched.

Acceptance:

```text
inventory worker starts
inventory worker sees inventory_jobs only
creative worker behavior unchanged
all inventory flags default false
```

**Stop after Phase 1.**

---

# PHASE 2 — Inventory models and migration

Goal: establish isolated persistence.

Create Inventory tables listed in section 7 and an Alembic migration.

Requirements:

- tenant-scoped constraints;
- idempotency uniqueness;
- no FK from Inventory page/AI/business models to generic Asset/AI/search models;
- optional allowed FK/read-only relation to existing `external_sources` for OAuth/source identity only.

Tests:

- migration up/down/re-up;
- tenant isolation;
- uniqueness/idempotency;
- creative repository tests unchanged.

**Stop after Phase 2.**

---

# PHASE 3 — Separate Drive poll/download flow

Goal: detect Inventory Inbox images without generic source sync.

Implement:

- settings validation for Drive source/folders;
- `InventoryDrivePoller`;
- `inventory_source_files` registration;
- `inventory_file_download` handler;
- content SHA-256;
- Inventory source storage namespace;
- supported MIME validation.

Tests:

- new file detected once;
- unchanged file not reprocessed;
- changed same Drive file creates a new provider version when appropriate;
- duplicate bytes detected safely;
- folder ignored;
- unsupported MIME ignored/rejected;
- generic `SourceAssetModel` count unchanged.

**Stop after Phase 3.**

---

# PHASE 4 — Inventory document preparation

Goal: transform downloaded submissions into Inventory documents/pages without generic assets.

Implement:

- page/submission models;
- stateless image preparation boundary;
- page/document grouping rules;
- `inventory_document_prepare` handler;
- duplicate submission state.

Do not call creative `asset_store` or generic analysis.

Tests include rotated/AVIF/JPEG/PNG/WebP cases supported by current runtime.

**Stop after Phase 4.**

---

# PHASE 5 — Separate Inventory AI

Goal: perform structured extraction with independent state/control.

Implement:

- `InventoryAiAnalysisModel`;
- Inventory extraction schema/version;
- Inventory prompt/version;
- Inventory AI gateway;
- Inventory-specific rate/concurrency/budget controls;
- `inventory_document_analyze` handler;
- structured JSON validation;
- safe retry/error classification.

Tests:

- exact profile/version is persisted;
- creative metadata profile activation has no effect;
- no `AssetAiAnalysisModel` row is created;
- Inventory AI pause does not pause creative AI;
- creative AI pause does not pause Inventory AI;
- provider errors remain isolated.

**Stop after Phase 5.**

---

# PHASE 6 — Normalize, validate and review backend

Goal: turn extraction into business-safe proposed records.

Implement:

- alias resolution;
- unit conversion;
- business validation;
- confidence decisions;
- review creation;
- approve/correct/reupload APIs;
- audit fields.

No inventory transaction is created from unresolved review data.

**Stop after Phase 6.**

---

# PHASE 7 — Inventory transactions

Goal: create authoritative stock movements.

Implement:

- opening/receipt/transfer/closing/waste semantics;
- linked transfer in/out;
- conversion snapshots;
- append-oriented auditability;
- idempotent transaction generation from approved lines.

Tests cover retry and concurrent approval without duplicate transactions.

**Stop after Phase 7.**

---

# PHASE 8 — Separate Inventory UI

Goal: expose Inventory operations without modifying creative Explorer behavior.

Implement routes/pages:

```text
/inventory
/inventory/inbox
/inventory/review
/inventory/daily
/inventory/reports
/inventory/settings
```

Required UI states:

```text
waiting
processing
needs review
needs reupload
approved
failed
finalized
```

Explorer and creative AI Operations continue to use current code paths.

**Stop after Phase 8.**

---

# PHASE 9 — Daily scheduler/finalization

Goal: run the business-day lifecycle independently.

Implement:

- dedicated Inventory scheduler;
- missing-document checks;
- 17:00 finalize logic;
- open-review blocking;
- forced-finalize with audit;
- daily report JSON.

Do not add tasks to creative source-sync scheduler.

**Stop after Phase 9.**

---

# PHASE 10 — Excel export and Drive archive

Goal: create reproducible Excel output and archive submissions.

Implement:

- openpyxl export from Inventory PostgreSQL snapshot;
- protected Sheet 4 fingerprint;
- upload to Inventory Drive output folder;
- archive/reupload move operations;
- idempotent export identity;
- retry without rewriting business transactions.

Tests prove Sheet 4 is unchanged.

**Stop after Phase 10.**

---

# PHASE 11 — Production isolation rollout

Goal: verify both pipelines can operate independently under failure.

Before enabling production automation:

1. use separate Inventory AI credentials/project where possible;
2. run Inventory in shadow mode;
3. compare manual and automated counts;
4. inject Inventory worker outage;
5. inject creative worker outage;
6. inject Inventory AI 429/provider failure;
7. inject Inventory Excel failure;
8. verify the other pipeline remains healthy in each case;
9. enable one tenant/store first;
10. expand only after stable observation.

**Stop after Phase 11.**

---

# 23. Files existing creative implementation should normally not require Inventory changes

Codex should treat these areas as protected unless a minimal platform hook is strictly necessary:

```text
apps/api/app/modules/pipeline/*
apps/api/app/modules/processing/*
apps/api/app/modules/processing_policy/*
apps/api/app/modules/ai_metadata/*
apps/api/app/modules/search/*
apps/api/app/modules/source_sync/*
apps/api/app/modules/assets/status_service.py
```

A change in one of these paths requires an explicit explanation in the PR:

```text
Why is this platform-level change necessary?
Why cannot Inventory implement it in its own module?
Which regression tests prove creative behavior is unchanged?
```

Default answer should be: **do not modify it**.

Allowed common-layer changes are narrow, for example:

- exposing an already-existing OAuth/token helper through a stable interface;
- extracting a truly stateless image transform utility;
- mounting the `/api/inventory` router;
- adding Inventory app startup/shutdown hooks;
- adding separate environment configuration.

---

# 24. Definition of done

Inventory Automation is complete only when:

```text
[ ] Existing creative CI is green.
[ ] Inventory uses a separate durable job table.
[ ] Inventory runs in a separate worker process/runtime.
[ ] Inventory has a separate scheduler.
[ ] Inventory Inbox is not processed by generic source sync.
[ ] Inventory submissions create no generic SourceAsset/Asset/Pipeline rows.
[ ] Inventory creates no AssetAiAnalysisModel rows.
[ ] Inventory creates no creative search/index documents.
[ ] Inventory pause/resume is independent.
[ ] Creative pause/resume is independent.
[ ] Inventory AI control/budget state is independent.
[ ] Production AI credentials are separable without code redesign.
[ ] Inventory review and transaction tables are tenant-safe.
[ ] Inventory daily finalize is idempotent.
[ ] Excel export is reproducible from PostgreSQL.
[ ] Sheet 4 remains unchanged.
[ ] Inventory worker outage does not stop creative worker.
[ ] Creative worker outage does not stop inventory worker.
[ ] Inventory queue backlog does not consume creative worker capacity.
[ ] Inventory AI failure does not modify creative processing controls.
[ ] Creative AI failure does not modify Inventory processing controls.
```

---

# 25. Codex prompt template

Use this structure for each implementation task:

```text
Read docs/inventory-automation-codex-plan.md first.
Treat it as the primary implementation architecture for Inventory Automation.

The Inventory flow MUST remain isolated from the existing Creative Asset flow.
Do not route Inventory through generic source_sync, processing_jobs, AssetModel,
AssetAiAnalysisModel, search projection, Elasticsearch, or creative processing policy.

Inspect the current branch before editing because source code overrides stale documentation.

Implement PHASE <N> only.

Do not implement future phases.
Do not refactor protected creative modules unless the phase explicitly requires a narrow platform hook.
Add tests proving both the requested behavior and pipeline isolation.
Run the relevant test groups.
Stop when the phase acceptance criteria are satisfied and report:
1. files changed,
2. migrations added,
3. tests run,
4. isolation guarantees verified,
5. remaining issues for the next phase.
```

---

# 26. Final architecture summary

The intended design is deliberately two-lane:

```text
CREATIVE LANE                         INVENTORY LANE
────────────────────                  ────────────────────
creative source sync                  inventory Drive poller
SourceAsset/Asset models              inventory_source_files
processing_jobs                       inventory_jobs
creative worker                       inventory worker
AssetAiAnalysisModel                  InventoryAiAnalysisModel
creative AI governance               inventory AI controls
search projection/index              normalize/validate/review
Explorer / AI Operations              Inventory UI
no inventory transactions             inventory transactions
no inventory Excel lifecycle          daily finalize + Excel
```

They share the road infrastructure, not the traffic lane.

A bug or backlog in one lane must not be able to consume, mutate, pause, complete, retry, index, or finalize work in the other lane.
