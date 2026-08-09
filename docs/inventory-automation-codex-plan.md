# Inventory Automation — Codex Implementation Plan

> Repository: `BaoNghiaNghia/creative-asset-manager`  
> Branch: `feature/google-drive-explorer-mvp`  
> Source baseline reviewed: `eac0d8a4c2b19eb1e34f624a73cf72d6f40ddbf8`  
> Baseline commit message: `update build fe`  
> Review date: 2026-08-09  
> Purpose: implementation plan for adding the Google Drive → AI → inventory → daily finalization → Excel workflow to the current codebase.

---

# 0. How Codex must use this document

This file is the **primary technical implementation plan** for the Inventory Automation feature.

Source-of-truth precedence:

1. **Current source code on the target branch** — always wins if the repository has changed after this document was written.
2. **`docs/inventory-automation-codex-plan.md`** — authoritative implementation sequencing and architecture for this feature.
3. **`docs/inventory-system-readiness-assessment.md`** — historical technical assessment; use for context only where it does not conflict with this plan or newer source.
4. **`docs/google-drive-inventory-automation.md`** — business/operational reference for forms, inventory rules, daily workflow, Excel behavior, and staff operations.

Codex rules:

- Re-read the current branch before starting every phase.
- Implement **one phase only per task/PR** unless explicitly instructed otherwise.
- Do not implement future phases opportunistically.
- Do not replace the existing source-sync, processing-job, AI-governance, tenant-policy, auth, or asset-storage infrastructure with a second queue/backend.
- Do not call OpenAI/Gemini SDKs directly from inventory business services.
- Do not let inventory files use the generic "latest active metadata profile" selection.
- Preserve tenant isolation, durable job semantics, idempotency, audit history, and current Explorer behavior.
- Existing creative-asset behavior must remain unchanged for files outside the configured inventory Inbox.
- Every phase must add/update tests and must stop when its acceptance criteria are met.
- Do not move to the next phase while relevant CI checks are red.

---

# 1. Current source assessment at `eac0d8a4...`

The repository already provides most infrastructure required by Inventory Automation. The new feature should be added as a domain on top of the existing platform rather than as a parallel application.

## 1.1. Components that must be reused

Current reusable foundations include:

- FastAPI application and tenant-aware authorization.
- PostgreSQL + SQLAlchemy + Alembic.
- Persistent Google OAuth connections.
- Google Drive read/write source connection.
- Google Drive incremental Changes API synchronization.
- `ExternalSourceModel`, `SourceAssetModel`, `AssetModel`, source-to-asset links.
- Durable `processing_jobs` queue with lease, retry, cancellation, idempotency and concurrency accounting.
- Worker handler registry and processing policy.
- Content deduplication.
- Managed asset storage.
- `AnalysisImagePreparer` for safe image normalization.
- `MetadataProfileModel` + `AssetAiAnalysisModel`.
- Gemini/OpenAI provider registry.
- AI rate-limit, concurrency, budget and emergency-stop governance.
- Existing React application shell, sidebar and explicit route mapping.

Do **not** introduce Celery, Redis queue, Google Apps Script orchestration, a second database, or a separate AI pipeline for the MVP.

---

# 2. Important deltas since the previous readiness assessment

The previous readiness document was based on commit `ac772df...`. There are additional commits after that baseline, including 15 commits after the readiness-document commit `2bea840...`.

The following changes materially affect Inventory planning.

## 2.1. Drive OAuth/reconnect is stronger

Current Google Drive source connections request:

```text
https://www.googleapis.com/auth/drive
```

and the source now includes more robust granted-scope normalization and persistence.

Useful existing functions include:

```text
resolve_granted_scopes(...)
validate_granted_scopes(..., require_write=True)
persist_drive_connection(...)
get_connection_access_token(..., require_drive_write_scope=True)
```

There is also a safe diagnostic command:

```text
apps/api/app/operations/google_oauth_diagnostic.py
```

Inventory preflight should reuse the same persisted source/connection instead of creating a new OAuth subsystem.

## 2.2. Previous Explorer upload `item_id` blocker is resolved

The prior assessment identified an undefined `item_id` cache invalidation after `POST /explorer/upload`.

That specific issue is no longer present in current source.

Do not carry that old blocker into implementation work.

## 2.3. Supported source image MIME types now include AVIF

Current Google Drive ingestion accepts:

```text
image/jpeg
image/png
image/webp
image/avif
```

HEIC/HEIF are still **not** accepted by source ingestion even though downstream `AnalysisImagePreparer` can decode more formats after managed storage.

MVP upload contract:

```text
JPEG / JPG
PNG
WebP
AVIF
```

HEIC support is explicitly out of scope for the MVP unless separately implemented in source ingestion and tested end-to-end.

## 2.4. Current CI failure is now known precisely

Current HEAD PR workflow run `31289305119` is red because the API/unit job fails.

Passing groups:

```text
Frontend checks                       PASS
PostgreSQL migrations/repositories    PASS
Elasticsearch integration             PASS
Durable pipeline end-to-end           PASS
```

Failing group:

```text
API, worker and provider unit tests    FAIL
Production release gate                FAIL (because upstream API/unit failed)
```

Exact current failures:

```text
ERROR
modules.ai_governance.test_multi_provider_governance
.MultiProviderGovernanceTest
.test_preclaim_honors_provider_mode_limit_and_runtime_stop

claimed is None where the test expects the first job to be claimed.
```

and:

```text
FAIL
modules.ai_operations.test_api
.AiOperationsApiTest
.test_summary_uses_canonical_current_job_states_and_latest_replacement

expected today["failed"] == 1
actual   today["failed"] == 0
```

These are **Phase 0 blockers**. Do not start Inventory domain implementation while the baseline test suite is knowingly red.

---

# 3. Target business workflow

Staff workflow remains intentionally simple:

```text
Write inventory values on the form
        ↓
Take clear photos of all pages
        ↓
Upload photos directly to one Google Drive Inbox folder
```

System workflow:

```text
Google Drive Inbox
        ↓
SourceSyncScheduler
        ↓
source_sync
        ↓
SourceAssetModel
        ↓
source_asset_download
        ↓
content dedup + AssetModel
        ↓
asset_store
        ↓
Inventory purpose routing
        ↓
inventory page/document creation
        ↓
explicit inventory AI profile
        ↓
AI extraction
        ↓
normalization
        ↓
business validation
        ↓
AUTO APPROVE / REVIEW / REUPLOAD
        ↓
inventory transactions
        ↓
daily run
        ↓
17:00 finalize/report
        ↓
Excel export
        ↓
Google Drive output + backup
```

---

# 4. Google Drive operating structure

Recommended folders:

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

MVP rule:

> Staff upload images **directly** into `00_INBOX_NHAN_VIEN`. No nested staff-created folders.

The source-sync mapper already stores Drive parent IDs in source metadata. The MVP can route an item with:

```python
inbox_folder_id in (source_asset.source_metadata or {}).get("parents", [])
```

No extra Google Drive API request is required for direct Inbox membership.

Nested/descendant Inbox routing may be added later using the existing ancestry/breadcrumb infrastructure.

---

# 5. Critical architecture decision: route Inventory before generic AI behavior

This is the most important implementation requirement.

Current asset pipeline behavior after storage can:

1. reuse any completed analysis for an `AssetModel`, or
2. if auto-analysis is enabled, select the newest active metadata profile and enqueue generic `asset_analyze`.

That behavior is correct for creative assets but unsafe for inventory submissions.

## Required behavior

Inventory purpose must be determined **before** both:

- generic completed-analysis reuse;
- generic latest-active-profile auto-analysis.

Required shape:

```text
Asset has been downloaded/stored
        ↓
AssetPurposeRouter
        ↓
Does this source occurrence belong to configured Inventory Inbox?
        │
        ├── NO
        │    ↓
        │  preserve existing creative-asset pipeline exactly
        │
        └── YES
             ↓
           inventory pipeline
             ↓
           DO NOT select generic active metadata profile
             ↓
           DO NOT reuse unrelated creative analysis as inventory extraction
```

## Important identity rule

Inventory routing must be based on the **source occurrence** (`SourceAssetModel`) and its folder binding, not only on `AssetModel`.

Reason:

```text
Drive file A in creative folder ─┐
                                 ├─ same bytes → same AssetModel
Drive file B in inventory Inbox ─┘
```

Creative content dedup is correct, but business intent is different.

Therefore:

- content dedup may reuse `AssetModel`;
- inventory submission/page identity must still point to `SourceAssetModel`;
- a completed creative AI analysis must never satisfy inventory extraction requirements.

---

# 6. Recommended Inventory module structure

Create:

```text
apps/api/app/modules/inventory/
├── __init__.py
├── model.py
├── schema.py
├── repository.py
├── service.py
├── router.py
├── permissions.py
├── settings_service.py
├── routing.py
├── extraction.py
├── normalization.py
├── validation.py
├── transactions.py
├── review_service.py
├── daily_run_service.py
├── report_service.py
├── excel_export_service.py
├── drive_archive_service.py
├── job_handlers.py
└── scheduler.py
```

Keep layers narrow. Do not create one `inventory_service.py` containing all behavior.

---

# 7. Data model

All inventory tables must be tenant-scoped.

Use PostgreSQL as the authoritative business-data store. Excel is a reproducible output, not the source of truth.

## 7.1. `inventory_settings`

One active settings row per tenant for MVP.

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
 metadata_profile_id
 timezone
 auto_approve_confidence
 review_confidence
 daily_missing_check_time
 daily_final_scan_time
 daily_finalize_time
 daily_export_time
 archive_enabled
 excel_export_enabled
 created_at
 updated_at
```

Constraints:

- unique `tenant_id` for MVP;
- `external_source_id` must belong to the same tenant;
- `metadata_profile_id` must belong to the same tenant;
- timezone defaults to `Asia/Ho_Chi_Minh`;
- thresholds must satisfy `0 <= review <= auto_approve <= 1`.

Do not store Google tokens in this table.

## 7.2. `inventory_locations`

Suggested initial codes:

```text
KHO_PHA_CHE
PHONG_PHA_CHE
KHO_PHONG_RANG
```

Fields:

```text
id
 tenant_id
 code
 name
 active
 created_at
 updated_at
```

Unique:

```text
tenant_id + code
```

## 7.3. `inventory_items`

Fields:

```text
id
 tenant_id
 code
 name
 category
 whole_unit
 fraction_unit
 base_unit
 conversion_factor
 active
 created_at
 updated_at
```

Do not retroactively mutate historical transaction quantity semantics when a conversion factor changes. Transactions must persist the actual conversion snapshot used.

## 7.4. `inventory_item_aliases`

Fields:

```text
id
 tenant_id
 item_id
 alias
 normalized_alias
 active
 created_at
 updated_at
```

Initial business aliases may include:

```text
TC OLONG   -> TRÂN CHÂU OLONG
RICHS (LÙN) -> RICH LÙN
CPHÊ MÁY   -> CÀ PHÊ PHA MÁY
BỘT Đ.XAY  -> BỘT BÉO ĐÁ XAY
```

Unknown names must **not** auto-create a new item.

## 7.5. `inventory_documents`

A logical inventory document, potentially containing multiple pages.

Fields:

```text
id
 tenant_id
 business_date
 document_type
 location_code
 document_reference
 expected_pages
 received_pages
 status
 submitted_by
 approved_by
 approved_at
 finalized_at
 created_at
 updated_at
```

Suggested document types:

```text
stock_count
warehouse_transfer
waste
```

Suggested statuses:

```text
collecting
analyzing
needs_review
needs_reupload
approved
rejected
finalized
```

## 7.6. `inventory_document_pages`

A page represents a specific source submission occurrence.

Fields:

```text
id
 tenant_id
 document_id
 source_asset_id
 asset_id
 drive_file_id
 content_hash
 page_number
 page_count
 analysis_id
 extraction_status
 raw_extraction_json
 submitted_at
 created_at
 updated_at
```

Required uniqueness:

```text
tenant_id + source_asset_id
```

This prevents the same source occurrence from entering inventory twice.

`content_hash` is still stored for business duplicate detection and audit.

## 7.7. `inventory_lines`

Fields:

```text
id
 tenant_id
 document_id
 page_id
 line_number
 raw_item_name
 item_id
 whole_quantity
 whole_unit
 fraction_quantity
 fraction_unit
 quantity_base_unit
 conversion_factor_snapshot
 waste_quantity
 waste_unit
 waste_quantity_base_unit
 waste_reason
 confidence
 validation_status
 review_note
 created_at
 updated_at
```

## 7.8. `inventory_reviews`

Fields:

```text
id
 tenant_id
 document_id
 page_id
 line_id
 review_type
 status
 proposed_json
 resolved_json
 reason
 assigned_to
 resolved_by
 resolved_at
 created_at
 updated_at
```

Suggested statuses:

```text
open
approved
corrected
reupload_requested
ignored
```

## 7.9. `inventory_transactions`

Fields:

```text
id
 tenant_id
 business_date
 location_id
 item_id
 transaction_type
 quantity_base_unit
 source_document_id
 source_line_id
 conversion_snapshot_json
 status
 created_at
 finalized_at
```

Transaction types:

```text
opening_balance
receipt
transfer_out
transfer_in
closing_count
waste
usage_adjustment
```

## 7.10. `inventory_daily_runs`

Fields:

```text
id
 tenant_id
 business_date
 status
 missing_locations_json
 open_reviews_count
 forced_finalize
 finalized_by
 finalized_at
 report_json
 version
 created_at
 updated_at
```

Unique:

```text
tenant_id + business_date
```

## 7.11. `inventory_exports`

Fields:

```text
id
 tenant_id
 business_month
 business_date
 export_type
 status
 drive_file_id
 drive_web_url
 content_hash
 source_template_file_id
 protected_sheet_fingerprint
 error_code
 error_message
 created_at
 completed_at
```

---

# 8. Business idempotency and duplicate rules

Do not rely only on generic `AssetModel` content dedup.

Required protections:

## 8.1. Source-occurrence idempotency

```text
unique tenant_id + source_asset_id
```

A source item can create at most one inventory page.

## 8.2. Job idempotency

Recommended keys:

```text
inventory-page-prepare:{page_id}:{content_hash}
inventory-analyze:{page_id}:{analysis_id}
inventory-normalize:{page_id}:{extraction_version}
inventory-validate:{document_id}:{document_version}
inventory-archive:{page_id}:{destination_folder_id}
inventory-finalize:{tenant_id}:{business_date}:{run_version}
inventory-export:{tenant_id}:{yyyy_mm}:{business_date}:{run_version}
inventory-report:{tenant_id}:{business_date}:{run_version}
```

## 8.3. Business duplicate detection

A second Drive file may have a different `source_asset_id` but identical bytes.

Do not blindly create a second business transaction.

At page prepare time:

```text
same tenant
+ same content_hash
+ same business_date/document context
```

must be evaluated for duplicate submission.

Safe default:

- mark second occurrence as `duplicate_submission` / needs review or ignored;
- preserve source history;
- do not create duplicate lines/transactions.

## 8.4. Archive/move behavior

Current source-sync behavior does not enqueue a new download job for a pure rename/move when content version is unchanged. That reduces loop risk.

Nevertheless, Inventory must remain idempotent even if future source behavior changes.

---

# 9. Multi-page document grouping rules

Do not guess aggressively when grouping pages.

## Stock count

Expected printed form title identifies location, for example:

```text
PHIẾU KIỂM KHO – PHÒNG PHA CHẾ
PHIẾU KIỂM KHO – KHO PHÒNG RANG
```

For a stock-count form, document identity may be grouped by:

```text
tenant + business_date + location + document_type
```

when page markers `Trang X/Y` are present and consistent.

## Transfer document

Expected title:

```text
PHIẾU XUẤT KHO PHA CHẾ
```

Prefer a printed reference/document number.

If a multi-page transfer has no reliable document reference and pages cannot be deterministically grouped, create review instead of guessing.

---

# 10. AI extraction profile

Create an explicit tenant-owned metadata profile, e.g.:

```text
profile_name: inventory_stock_sheet
profile_version: 1
prompt_version: inventory-v1
pipeline_version: inventory-v1
```

`inventory_settings.metadata_profile_id` must point to the exact profile.

Do not resolve by:

```text
active = true
ORDER BY created_at DESC
LIMIT 1
```

## 10.1. Example extraction schema

```json
{
  "document_type": "stock_count",
  "business_date": "2026-08-09",
  "location_code": "PHONG_PHA_CHE",
  "document_reference": null,
  "page_number": 1,
  "page_count": 2,
  "employee_name": null,
  "rows": [
    {
      "line_number": 1,
      "raw_item_name": "TC OLONG",
      "whole_quantity": 1,
      "whole_unit": "gói",
      "fraction_quantity": 250,
      "fraction_unit": "gram",
      "waste_quantity": 0,
      "waste_unit": null,
      "waste_reason": null,
      "confidence": 0.97,
      "notes": null
    }
  ],
  "warnings": []
}
```

## 10.2. Prompt constraints

- Read only what is visible.
- Never invent unreadable quantities.
- Preserve `raw_item_name` from the photo.
- Capture handwritten notes outside the printed table when relevant.
- Extract whole and fractional quantity separately.
- Extract waste amount and reason.
- Detect page number/page count.
- If crossed-out/replaced values are ambiguous, output a warning instead of guessing.
- JSON only, conforming to profile schema.

---

# 11. AI execution must reuse existing governance

Inventory AI must reuse:

- `AssetAiAnalysisModel`;
- `AiAnalysisService`;
- `AnalysisImagePreparer`;
- provider registry;
- model allowlist;
- provider policy;
- rate limits;
- budget reservations;
- project quota;
- runtime emergency stop;
- analysis lease;
- structured-output validation.

Do not call provider SDKs directly from `inventory/job_handlers.py`.

## 11.1. Recommended lifecycle

`inventory_page_prepare` must create the AI analysis record **before** enqueueing the AI job.

```text
inventory_page_prepare
   ↓
resolve exact inventory metadata_profile_id
   ↓
create/reuse AssetAiAnalysisModel
   ↓
store analysis_id on inventory_document_pages
   ↓
enqueue inventory_document_analyze
        payload:
        - analysis_id
        - inventory_page_id
```

The AI job must have an `analysis_id` available at claim time because the current worker claim path can reserve a model-start slot before the handler runs.

## 11.2. Inventory AI handler

Recommended behavior:

```text
inventory_document_analyze
   ↓
load persisted AssetAiAnalysisModel
   ↓
resolve exact persisted AI provider
   ↓
call shared AiAnalysisService(..., enqueue_index=False)
   ↓
completed metadata_json
   ↓
persist extraction result/reference on inventory page
   ↓
enqueue inventory_document_normalize
```

Inventory extraction must **not** automatically build creative search projection or index the extraction into Search V3 as if it were creative metadata.

---

# 12. Processing-job integration — exact cross-cutting requirements

Adding a new handler is not sufficient. Current worker eligibility is fail-closed.

## 12.1. Job types

Modify:

```text
apps/api/app/domain/processing/types.py
```

Add:

```text
inventory_page_prepare
inventory_document_analyze
inventory_document_normalize
inventory_document_validate
inventory_document_archive
inventory_daily_finalize
inventory_excel_export
inventory_daily_report
```

## 12.2. Worker global feature flags

Modify:

```text
apps/api/app/modules/processing/bootstrap.py
```

Add `_JOB_GLOBAL_FLAGS` entries and register every handler in `build_handler_registry(...)`.

Recommended global gating:

```text
inventory_page_prepare       -> PROCESSING_JOBS_ENABLED + INVENTORY_AUTOMATION_ENABLED
inventory_document_analyze   -> PROCESSING_JOBS_ENABLED + INVENTORY_AUTOMATION_ENABLED + DYNAMIC_AI_METADATA_ENABLED + AI_SINGLE_ANALYSIS_ENABLED
inventory_document_normalize -> PROCESSING_JOBS_ENABLED + INVENTORY_AUTOMATION_ENABLED
inventory_document_validate  -> PROCESSING_JOBS_ENABLED + INVENTORY_AUTOMATION_ENABLED
inventory_document_archive   -> PROCESSING_JOBS_ENABLED + INVENTORY_AUTOMATION_ENABLED + INVENTORY_DRIVE_ARCHIVE_ENABLED
inventory_daily_finalize     -> PROCESSING_JOBS_ENABLED + INVENTORY_AUTOMATION_ENABLED
inventory_excel_export       -> PROCESSING_JOBS_ENABLED + INVENTORY_AUTOMATION_ENABLED + INVENTORY_EXCEL_EXPORT_ENABLED
inventory_daily_report       -> PROCESSING_JOBS_ENABLED + INVENTORY_AUTOMATION_ENABLED
```

## 12.3. Tenant-aware job claimer

Modify:

```text
apps/api/app/modules/processing_policy/claim.py
```

This is mandatory.

Current `_stage_enabled()` only allows job types represented in `STAGE_POLICY`. A new inventory job omitted from that map will never be claimed.

For MVP, avoid schema expansion of the tenant processing-policy table unless necessary.

Recommended mappings:

```text
inventory_page_prepare       -> pipeline_enabled
inventory_document_analyze   -> ai_analysis_enabled
inventory_document_normalize -> pipeline_enabled
inventory_document_validate  -> pipeline_enabled
inventory_document_archive   -> pipeline_enabled
inventory_daily_finalize     -> pipeline_enabled
inventory_excel_export       -> pipeline_enabled
inventory_daily_report       -> pipeline_enabled
```

## 12.4. AI job category

Do not let `inventory_document_analyze` bypass AI concurrency/governance.

Recommended refactor:

```python
SINGLE_AI_JOB_TYPES = (
    "asset_analyze",
    "inventory_document_analyze",
)

BATCH_AI_JOB_TYPES = (
    "ai_batch_prepare",
    "ai_batch_submit",
    "ai_batch_poll",
    "ai_batch_import",
    "ai_batch_retry_items",
)

AI_JOB_TYPES = SINGLE_AI_JOB_TYPES + BATCH_AI_JOB_TYPES
```

Update semantically relevant checks that are currently hard-coded to:

```python
job.job_type == "asset_analyze"
```

so the inventory single-analysis job receives:

- tenant AI concurrency accounting;
- provider `single_enabled` gating;
- provider `single_active_jobs_limit`;
- provider emergency stop;
- global runtime AI stop;
- model-start reservation/rate limit.

Generalize analysis-ID resolution so `inventory_document_analyze` resolves `payload["analysis_id"]` deterministically.

Do not weaken fail-closed behavior for unknown job types.

---

# 13. Processing policy recommendation for MVP

Do **not** add a large set of inventory-specific processing-policy database columns in Phase 1.

Use:

```text
Global feature flag:
INVENTORY_AUTOMATION_ENABLED

Tenant feature state:
inventory_settings.enabled

Existing processing policy:
pipeline_enabled
ai_analysis_enabled
processing_paused
provider policy / emergency stop
```

This gives Inventory:

- platform-level kill switch;
- tenant-level enable/disable;
- existing global tenant pause;
- existing AI policy and budget enforcement.

Independent Inventory-specific concurrency controls can be added in a later iteration after operational data exists.

---

# 14. Normalization and validation

## 14.1. Item matching order

Use deterministic matching order:

```text
1. exact item code
2. exact normalized canonical name
3. exact normalized alias
4. safe fuzzy candidate only if uniquely above threshold
5. otherwise review
```

Never automatically create an inventory item because AI returned a new string.

## 14.2. Units

Persist both source values and normalized base-unit values.

Example:

```text
source whole_quantity       = 1
source whole_unit           = bag
source fraction_quantity    = 250
source fraction_unit        = gram
base quantity               = calculated normalized amount
conversion factor snapshot  = factor used at this moment
```

## 14.3. Confidence policy

Initial configuration:

```text
confidence >= 0.95 and all validations pass -> auto approve
0.80 <= confidence < 0.95                  -> review
confidence < 0.80                          -> review/reupload
```

Confidence never overrides business validation.

## 14.4. Mandatory validation rules

At minimum:

- no negative quantities;
- recognized item required for auto approval;
- valid unit required;
- page count must be complete before document finalization;
- duplicate source/page rejected;
- waste quantity with no waste reason -> review;
- conflicting page numbering -> review;
- implausible large delta -> review;
- negative calculated usage -> anomaly/review; never clamp to zero;
- transfer must create both source and destination legs atomically.

---

# 15. Inventory transaction rules

For warehouse transfer from `KHO_PHA_CHE` to `PHONG_PHA_CHE`, create both records in one database transaction:

```text
KHO_PHA_CHE   -> transfer_out
PHONG_PHA_CHE -> transfer_in
```

Both must reference the same source document.

If either insert fails, rollback both.

Usage formula:

```text
Usage = Opening
      + Receipt
      + Transfer In
      - Transfer Out
      - Closing
      - Waste
```

A simplified display may roll transfer into receipt, but authoritative transactions should keep transfers separate for auditability.

Do not generate business transactions from a document until it is approved according to validation/review policy.

---

# 16. Daily run semantics

Default timezone:

```text
Asia/Ho_Chi_Minh
```

Recommended operating schedule:

```text
14:00  staff reminder — notification integration may remain external for MVP
16:30  missing-location/page check
16:50  final source-sync request / readiness check
17:00  daily finalize attempt
17:10  report/export/archive attempt
```

Important rule:

> Process all unprocessed source occurrences, not only files whose Drive timestamp equals today.

`business_date` precedence:

1. date confidently extracted from the form;
2. source upload/discovery date converted to configured timezone;
3. manager correction during review.

Daily finalize must not silently discard open review items or incomplete pages.

Possible states:

```text
collecting
awaiting_review
ready_to_finalize
finalized
finalized_with_override
export_failed
completed
```

Forced finalization must record actor and reason.

---

# 17. Drive archive design

Use a background service/worker directly against the tenant Drive source with an explicitly write-scoped connection.

Do **not** call Explorer HTTP endpoints from the worker.

Use the existing source resolver/provider boundary or create a narrow server-side Inventory Drive service around it.

Before mutation:

```text
get_connection_access_token(..., require_drive_write_scope=True)
```

or equivalent tenant-source resolver path.

Archive behavior:

```text
approved/finalized page -> 01_DA_XU_LY/YYYY-MM-DD
needs_reupload          -> 02_CAN_CHUP_LAI/YYYY-MM-DD
```

The archive service must be idempotent:

- if already in target folder -> success;
- provider 429/5xx -> retryable;
- 401/403 -> stable connection/write-permission error;
- missing source file -> review/failure state, not destructive recreation.

Legacy readonly connections must fail preflight before automation is enabled.

---

# 18. Excel export design

## 18.1. Dependency

Current backend requirements do not include `openpyxl`.

Add and pin it during the Excel phase only.

## 18.2. Source of truth

```text
PostgreSQL = authoritative inventory state
Excel      = generated/reproducible output
```

Never read a manually modified workbook back as the authoritative inventory ledger unless a later feature explicitly defines reconciliation.

## 18.3. Protected Sheet 4

Existing business requirement:

```text
Sheet 4: Báo cáo sử dụng NVL trong ca
```

must remain unchanged.

Exporter must use an allowlist of sheets it is permitted to modify.

Do not hard-code assumptions about workbook sheet order without first loading the configured template and validating names.

At minimum, support existing business sheets:

```text
KHO PHA CHẾ
PHÒNG PHA CHẾ
KHO PHÒNG RANG
```

Potential extra generated sheets:

```text
NHẬT KÝ HẰNG NGÀY
BÁO CÁO NGẮN
DANH MỤC HÀNG
CHỜ XÁC NHẬN
```

## 18.4. Safe export algorithm

```text
1. resolve exact configured template/current workbook
2. download bytes
3. fingerprint protected sheet/package state
4. write to temporary workbook using openpyxl
5. modify allowlisted sheets only
6. save temporary output
7. reopen and validate workbook
8. verify protected Sheet 4 invariant
9. calculate output hash
10. upload versioned output/backup
11. persist Drive file ID + hash in inventory_exports
12. only then mark export completed
```

If validation fails, keep database finalization intact and mark export failed so it can be retried.

## 18.5. Current Drive provider limitation to address explicitly

The current `GoogleDriveClient` clearly supports creating/uploading a new file, moving, copying and deleting.

Do not assume `upload_file(...)` updates an existing Drive file in-place.

For the safest first implementation, export versioned files and persist the returned Drive file ID.

Example:

```text
KIEM_HANG_2026_08_2026-08-09_1710.xlsx
```

and/or daily backup:

```text
BAN_SAO_2026-08-09_1710.xlsx
```

If the product requires a stable "main workbook" Drive file ID, implement an explicit provider method for replacing/updating Drive file content and cover it with provider tests before using it in production.

## 18.6. Sheet-4 regression test

At minimum compare before/after:

- cell values/formulas;
- merged ranges;
- row/column dimensions;
- styles where relevant.

If the real workbook contains charts, images, macros, named ranges or advanced Excel objects that must survive byte-for-byte/package-level transformations, add a real sanitized fixture and stronger ZIP/XML package regression tests before production rollout.

---

# 19. API surface

Suggested endpoints under `/api/inventory`.

## Settings

```text
GET  /api/inventory/settings
PUT  /api/inventory/settings
POST /api/inventory/settings/test-drive
POST /api/inventory/settings/test-profile
```

`test-drive` must verify:

- source belongs to tenant;
- Inbox exists/is folder;
- configured output folders exist;
- write scope is available when archive/export is enabled;
- account can actually write required folders.

## Today / daily run

```text
GET  /api/inventory/daily-runs/{date}
POST /api/inventory/daily-runs/{date}/scan
POST /api/inventory/daily-runs/{date}/finalize
POST /api/inventory/daily-runs/{date}/reopen
GET  /api/inventory/daily-runs/{date}/report
```

## Documents

```text
GET  /api/inventory/documents
GET  /api/inventory/documents/{id}
POST /api/inventory/documents/{id}/reanalyze
POST /api/inventory/documents/{id}/approve
POST /api/inventory/documents/{id}/reject
```

## Reviews

```text
GET   /api/inventory/reviews
PATCH /api/inventory/reviews/{id}
POST  /api/inventory/reviews/{id}/approve
POST  /api/inventory/reviews/{id}/request-reupload
```

## Master data

```text
GET/POST/PATCH /api/inventory/items
GET/POST/DELETE /api/inventory/item-aliases
GET /api/inventory/locations
```

## Export

```text
GET  /api/inventory/exports
POST /api/inventory/exports/{date}/retry
```

Mutation endpoints must be tenant-scoped, permission-guarded and auditable.

---

# 20. Permissions

Create explicit permissions:

```text
inventory.view
inventory.review
inventory.manage_items
inventory.finalize
inventory.export
inventory.configure
```

Suggested role intent:

```text
Viewer         -> inventory.view only when intentionally granted
Operator       -> view + review
Tenant Admin   -> all tenant inventory permissions
Platform Admin -> platform override per existing authorization model
```

Do not piggyback all mutations on `assets.manage`.

Seed permissions through the repository's existing RBAC migration/seed pattern and test least privilege.

---

# 21. React routes/pages

Current application route map does not include Inventory.

Add explicit routes, for example:

```text
/inventory
/inventory/review
/inventory/items
/inventory/reports
/inventory/settings
```

Suggested pages:

```text
InventoryTodayPage
InventoryReviewPage
InventoryItemsPage
InventoryReportsPage
InventorySettingsPage
```

Do not overload creative Explorer processing status to represent business review state.

Explorer can link to Inventory context where useful, but Inventory business screens should own their own statuses.

---

# 22. Feature flags and configuration

Add to `Settings.FEATURE_FLAG_NAMES`, `.env.example`, and `deploy/production.env.example`:

```dotenv
INVENTORY_AUTOMATION_ENABLED=false
INVENTORY_EXCEL_EXPORT_ENABLED=false
INVENTORY_DRIVE_ARCHIVE_ENABLED=false
```

Prefer tenant-specific folder IDs, profile ID, times, confidence thresholds and timezone in `inventory_settings`, not as global environment variables.

Global flags are upper-bound kill switches only.

Production defaults must remain fail-closed (`false`).

---

# 23. Files that Codex will likely modify across phases

Do not modify all of these in one PR. This list is a dependency map.

Backend core/cross-cutting:

```text
apps/api/app/core/config.py
apps/api/app/main.py
apps/api/app/domain/processing/types.py
apps/api/app/modules/processing/bootstrap.py
apps/api/app/modules/processing_policy/claim.py
apps/api/app/modules/pipeline/handlers.py
apps/api/requirements.txt
.env.example
deploy/production.env.example
```

New Inventory domain:

```text
apps/api/app/modules/inventory/**
database/migrations/versions/**
```

Google Drive integration as needed:

```text
apps/api/app/modules/explorer/tenant_source.py
apps/api/app/providers/google/drive.py
```

Frontend:

```text
apps/client/app/AppRoute.tsx
apps/client/app/App.tsx or shared shell/navigation
apps/client/app/inventory/**
apps/client/features/inventory/**
apps/client/styles/inventory.css
```

Tests:

```text
apps/api/tests/modules/inventory/**
apps/api/tests/modules/processing_policy/**
apps/api/tests/modules/pipeline/**
apps/api/tests/providers/**
apps/api/tests/integration/**
apps/client/app/inventory/**/*.test.tsx
```

---

# 24. Implementation phases

Each phase below should normally be one focused PR/task.

---

## PHASE 0 — Restore green baseline

### Goal

Return current branch to a known-green baseline before adding Inventory code.

### Scope

Investigate and fix only the current regressions responsible for:

```text
test_preclaim_honors_provider_mode_limit_and_runtime_stop

test_summary_uses_canonical_current_job_states_and_latest_replacement
```

Likely touch areas:

```text
apps/api/app/modules/processing/repository.py
apps/api/app/modules/processing/runtime.py
apps/api/app/modules/processing_policy/claim.py
apps/api/app/modules/ai_operations/**
```

Do not add Inventory tables/jobs/features in this phase.

### Tests

Run at minimum the two failing tests directly, then full API/unit suite.

CI acceptance:

```text
Frontend checks                       PASS
API, worker and provider unit tests   PASS
PostgreSQL migrations/repositories    PASS
Elasticsearch integration             PASS
Durable pipeline end-to-end           PASS
Production release gate               PASS
```

### Stop boundary

Stop after baseline CI is green.

---

## PHASE 1 — Inventory domain schema, repositories and permissions

### Goal

Introduce durable Inventory data structures with no automatic processing yet.

### Create

```text
apps/api/app/modules/inventory/model.py
apps/api/app/modules/inventory/schema.py
apps/api/app/modules/inventory/repository.py
apps/api/app/modules/inventory/permissions.py
apps/api/app/modules/inventory/router.py
```

Create Alembic migration for the domain tables described in this plan.

Seed Inventory permissions using existing RBAC patterns.

### Requirements

- every repository query tenant-scoped;
- unique constraints enforce idempotency fundamentals;
- no Google API calls;
- no AI calls;
- no worker jobs yet;
- no automatic scheduler.

### Tests

- migration upgrade/downgrade/re-upgrade;
- tenant isolation;
- unique source page constraint;
- item/alias constraints;
- daily-run uniqueness;
- permission boundaries.

### Acceptance criteria

- database at one Alembic head;
- no existing tests regress;
- Inventory CRUD/master data works behind explicit permissions;
- feature remains inactive by default.

### Stop boundary

Stop before Drive Inbox routing.

---

## PHASE 2 — Inventory settings, Drive binding and purpose routing

### Goal

Allow a tenant admin to configure one Inventory Inbox and route matching source occurrences into Inventory without calling AI.

### Create/modify

```text
apps/api/app/modules/inventory/settings_service.py
apps/api/app/modules/inventory/routing.py
apps/api/app/modules/inventory/router.py
apps/api/app/modules/pipeline/handlers.py
apps/api/app/core/config.py
.env.example
deploy/production.env.example
```

### Critical routing requirement

Modify post-storage orchestration so inventory purpose is checked **before** generic completed-analysis reuse and generic auto-analysis.

Preserve current behavior exactly for non-inventory assets.

### Drive settings test endpoint

Add a preflight that confirms configured source/folders and write capability when applicable.

Reuse existing OAuth/source resolver infrastructure.

### Tests

- direct Inbox parent routes to Inventory;
- sibling folder remains creative pipeline;
- same `AssetModel` linked from creative + Inventory source occurrence routes each occurrence correctly;
- generic creative analysis is not reused as Inventory extraction;
- readonly legacy connection is rejected when write-required settings are enabled;
- disabled `inventory_settings.enabled` leaves current pipeline unchanged.

### Acceptance criteria

A photo in Inbox results in a durable Inventory page/submission record, but no AI call yet.

### Stop boundary

Stop before adding Inventory worker jobs.

---

## PHASE 3 — Durable Inventory job plumbing

### Goal

Register and make Inventory jobs safely claimable using current worker infrastructure.

### Modify

```text
apps/api/app/domain/processing/types.py
apps/api/app/modules/processing/bootstrap.py
apps/api/app/modules/processing_policy/claim.py
apps/api/app/core/config.py
```

Create skeleton handlers in:

```text
apps/api/app/modules/inventory/job_handlers.py
```

### Requirements

- add JobType enum values;
- add global flag mappings;
- register handlers explicitly;
- add `STAGE_POLICY` mappings;
- include Inventory AI job in single-AI concurrency/provider/runtime-stop semantics;
- all non-AI Inventory jobs remain bounded by total tenant job concurrency;
- unknown job types remain fail-closed.

### Tests

- every new job type is registered;
- disabled global Inventory flag prevents claims;
- disabled tenant Inventory setting prevents producers from enqueueing;
- tenant pause blocks jobs;
- Inventory AI job respects AI active-job limit;
- provider single limit blocks Inventory AI exactly like `asset_analyze`;
- emergency stop blocks Inventory AI but not unrelated non-AI jobs;
- inventory `analysis_id` model-slot resolution is deterministic.

### Acceptance criteria

Skeleton Inventory jobs can be enqueued, claimed and completed without AI/business behavior.

### Stop boundary

Stop before real AI extraction.

---

## PHASE 4 — Explicit Inventory AI extraction

### Goal

Analyze Inventory photos with the exact configured Inventory profile while reusing existing AI governance.

### Create/modify

```text
apps/api/app/modules/inventory/extraction.py
apps/api/app/modules/inventory/job_handlers.py
apps/api/app/modules/ai_metadata/service.py   # only if a small reusable extension is required
apps/api/app/modules/processing_policy/claim.py
```

### Requirements

- create/reuse exact Inventory `MetadataProfileModel`;
- persist configured `metadata_profile_id`;
- create `AssetAiAnalysisModel` before AI job enqueue;
- call shared `AiAnalysisService`;
- `enqueue_index=False` for Inventory extraction;
- no creative Search V3 projection/index side effect;
- use existing `AnalysisImagePreparer`;
- persist extraction result/version on Inventory page;
- enqueue normalization only after completed valid analysis.

### Tests

- explicit profile selected even when a newer creative profile exists;
- completed creative analysis does not satisfy Inventory analysis;
- provider/model governance applies;
- budget block is represented safely;
- retry/defer preserves page/job idempotency;
- schema-invalid output never reaches normalization;
- no Search V3 indexing is enqueued from Inventory extraction.

### Acceptance criteria

A supported image in Inbox produces a valid structured Inventory extraction or a stable review/failure state with no manual DB intervention.

### Stop boundary

Stop before matching item master and creating business transactions.

---

## PHASE 5 — Normalization, validation and review backend

### Goal

Convert AI extraction into safe business-domain lines.

### Create

```text
apps/api/app/modules/inventory/normalization.py
apps/api/app/modules/inventory/validation.py
apps/api/app/modules/inventory/review_service.py
```

### Requirements

- deterministic item/alias matching;
- unit conversion snapshot;
- confidence policy;
- business validation rules;
- multi-page completeness;
- duplicate submission handling;
- review records;
- unknown items do not auto-create master data.

### Tests

Include at least:

```text
TC OLONG -> TRÂN CHÂU OLONG
RICHS (LÙN) -> RICH LÙN
CPHÊ MÁY -> CÀ PHÊ PHA MÁY
BỘT Đ.XAY -> BỘT BÉO ĐÁ XAY
```

plus:

- ambiguous alias;
- missing waste reason;
- negative quantity;
- incomplete page set;
- confidence boundaries;
- duplicate content submission.

### Acceptance criteria

Each analyzed document ends as either:

```text
approved
needs_review
needs_reupload
```

with deterministic reasons.

### Stop boundary

Stop before frontend review UI or ledger transactions.

---

## PHASE 6 — Inventory UI and review workflow

### Goal

Give operators a safe interface to review/edit/approve extracted values.

### Add routes

```text
/inventory
/inventory/review
/inventory/items
/inventory/settings
```

### Create frontend area

```text
apps/client/app/inventory/**
apps/client/features/inventory/**
```

### Requirements

Inventory Today:

- received/missing locations;
- page/document count;
- open review count;
- last scan;
- Drive connection/preflight state.

Review:

- source image/preview;
- extracted row;
- matched item;
- quantities/units;
- confidence/warnings;
- approve/correct/request reupload.

Settings:

- source/folder binding;
- explicit AI profile;
- thresholds;
- timezone/times;
- archive/export switches;
- preflight result.

### Tests

- route mapping;
- RBAC;
- review corrections persist before/after values;
- viewer cannot mutate;
- failed Drive preflight prevents automation enablement.

### Acceptance criteria

An operator can resolve all open reviews without touching database/Google Sheets/Excel manually.

### Stop boundary

Stop before creating authoritative inventory transactions.

---

## PHASE 7 — Inventory transactions and daily ledger calculation

### Goal

Turn approved documents into auditable inventory transactions.

### Create

```text
apps/api/app/modules/inventory/transactions.py
apps/api/app/modules/inventory/daily_run_service.py
```

### Requirements

- transaction generation idempotent;
- approved line → deterministic transaction(s);
- transfer pair atomic;
- waste separate from usage;
- conversion snapshot stored;
- finalized transaction history immutable except explicit reversal/correction policy.

### Tests

- stock count;
- transfer out/in atomic pair;
- transaction rollback on one-leg failure;
- waste reason;
- usage calculation;
- negative usage anomaly;
- rerun does not duplicate ledger entries.

### Acceptance criteria

For a manually initiated day, database produces a reproducible inventory ledger and daily calculation.

### Stop boundary

Stop before automatic time-based finalization.

---

## PHASE 8 — Inventory scheduler and daily finalization

### Goal

Automate missing checks, final scan/finalize and follow-up jobs.

### Create

```text
apps/api/app/modules/inventory/scheduler.py
```

### Worker integration

Run the Inventory scheduler in the worker/scheduler process, not in every API replica.

Follow existing `SourceSyncScheduler` lifecycle conventions.

### Schedule

Use tenant `inventory_settings` timezone/times.

At minimum:

```text
16:30 missing check
16:50 source-sync/final readiness request
17:00 finalize attempt
17:10 report/export/archive enqueue
```

### Requirements

- one schedule action per tenant/date/time bucket;
- database idempotency prevents duplicate scheduler instances;
- open required reviews block normal finalize;
- incomplete required documents block normal finalize;
- forced finalize is explicit and audited;
- a failed export must not roll back finalized inventory ledger.

### Tests

- two scheduler instances coalesce;
- timezone/day-boundary behavior;
- missing page blocks finalize;
- open review blocks finalize;
- forced finalize actor/reason;
- retry after worker restart.

### Acceptance criteria

The system can autonomously reach `finalized` or a clearly actionable blocked state every business day.

### Stop boundary

Stop before Excel writing/Drive archive if those flags remain disabled.

---

## PHASE 9 — Excel export, Drive output and archive

### Goal

Generate business Excel output while preserving protected workbook content, then archive source photos.

### Create/modify

```text
apps/api/app/modules/inventory/excel_export_service.py
apps/api/app/modules/inventory/drive_archive_service.py
apps/api/requirements.txt
apps/api/app/providers/google/drive.py   # only if explicit file-update capability is required
```

### Requirements

- pin `openpyxl`;
- download configured workbook/template;
- allowlist modified sheets;
- protect Sheet 4;
- temporary output + reopen validation;
- Drive output uses explicit write-scoped token;
- worker uses provider/service directly, not Explorer HTTP API;
- export job idempotent;
- archive job idempotent;
- output Drive IDs/hashes persisted.

### Safer initial production behavior

Prefer versioned output files before implementing in-place main-file replacement.

### Tests

- template not found;
- readonly connection;
- folder write denied;
- retryable Google failure;
- repeated export returns/reuses correct logical output without duplicate ledger state;
- protected Sheet 4 fingerprint unchanged;
- workbook opens after export;
- archive already completed;
- archive move generates no duplicate business processing.

### Acceptance criteria

A finalized day can regenerate its Excel output from PostgreSQL and source configuration without manual data entry.

### Stop boundary

Stop before full production enablement.

---

## PHASE 10 — Shadow rollout and production activation

### Goal

Validate business accuracy before allowing Inventory Automation to become authoritative operationally.

### Shadow period

Run new automation in parallel with current manual process for 7–14 business days.

Track:

```text
files discovered
files processed
missing files/pages
duplicate submissions
AI auto-approved lines
reviewed/corrected lines
item-match corrections
quantity corrections
waste corrections
transaction differences
Excel differences
AI cost
processing latency
```

### Go-live gates

- no unexplained lost source files;
- duplicate prevention verified;
- review queue manageable;
- transaction calculations reconciled;
- protected Sheet 4 verified on real sanitized workbook fixture;
- Drive reconnect/preflight runbook tested;
- AI emergency stop tested;
- Inventory global kill switch tested;
- full CI green;
- backup/restore/export retry tested.

### Production flags

Enable in stages:

```text
INVENTORY_AUTOMATION_ENABLED=true
```

then after shadow validation:

```text
INVENTORY_EXCEL_EXPORT_ENABLED=true
```

then last:

```text
INVENTORY_DRIVE_ARCHIVE_ENABLED=true
```

Do not enable all three for the first production trial.

---

# 25. Test fixture requirements

Create a sanitized Inventory fixture set including:

- straight photo;
- rotated photo;
- dark photo;
- perspective/skew;
- handwritten numbers;
- crossed-out corrected number;
- handwritten note outside table;
- page 1/2 and page 2/2;
- missing page;
- duplicate image upload;
- unknown alias;
- waste with reason;
- waste without reason;
- transfer slip;
- AVIF image;
- unsupported HEIC case;
- same image bytes appearing in creative folder and Inventory Inbox.

Do not use production staff/customer personal data in committed fixtures.

---

# 26. Observability

Inventory should emit bounded, tenant-safe metrics/log events.

Recommended metrics:

```text
inventory_files_routed
inventory_duplicate_submissions
inventory_pages_analyzed
inventory_ai_deferred
inventory_ai_failed
inventory_documents_needs_review
inventory_documents_needs_reupload
inventory_lines_auto_approved
inventory_lines_corrected
inventory_daily_finalize_success
inventory_daily_finalize_blocked
inventory_excel_export_success
inventory_excel_export_failed
inventory_archive_success
inventory_archive_failed
```

Useful latency:

```text
upload/discovery -> page created
page created -> AI completed
AI completed -> approved/review
business date finalize duration
Excel export duration
```

Do not log OAuth tokens, raw signed URLs, full raw provider responses or personal data.

---

# 27. Security and data handling

- Persist no Drive tokens in Inventory tables.
- Reuse encrypted OAuth persistence.
- All repository methods tenant-scope queries.
- Write mutations require explicit Inventory permissions.
- Review corrections record actor + before/after values.
- Forced finalize records actor + reason.
- Folder IDs are configuration, not authorization by themselves.
- Drive provider access must still resolve through tenant-owned connection.
- Raw AI response retention follows existing AI retention configuration.
- Inventory photo retention must be configurable operationally; default policy can be defined later, but no silent deletion in MVP.

---

# 28. Non-goals for the first production version

Do not include unless explicitly requested:

- Zalo OA/GMF integration;
- Zalo PC automation;
- OCR provider separate from the existing AI provider layer;
- native mobile app;
- multiple independent Inventory Inboxes per tenant;
- HEIC source ingestion;
- multi-store corporate consolidation dashboard;
- automatic procurement/reorder suggestions;
- direct POS integration;
- autonomous creation of new item master rows;
- automatic destructive deletion of source photos;
- replacing PostgreSQL with Google Sheets/Excel.

---

# 29. Definition of Ready for each Codex phase

Before starting a phase:

- current HEAD has been re-read;
- previous phase is merged/applied;
- relevant CI is green;
- feature flags remain fail-closed unless that phase explicitly enables local/test behavior;
- migrations are at one head;
- no undocumented manual DB changes are required.

---

# 30. Definition of Done for each Codex phase

A phase is complete only when:

- implementation matches that phase only;
- tests cover success + failure + tenant isolation + idempotency where applicable;
- relevant existing tests pass;
- no new secrets/config values are hard-coded;
- migrations/config examples are updated;
- feature remains disabled by default unless explicitly part of rollout;
- docs/checklist implementation log is updated;
- Codex stops and reports what changed, tests run, and what remains for the next phase.

---

# 31. Implementation log template

Codex should append a short entry after completing each phase instead of rewriting historical entries.

```text
## Implementation Log

### Phase N — YYYY-MM-DD — <commit SHA>
Status: completed / partial / blocked

Implemented:
- ...

Tests:
- ...

Configuration/migrations:
- ...

Known follow-ups for next phase:
- ...
```

Do not mark a phase complete if CI relevant to that phase is still red.

---

# 32. Ready-to-use Codex instruction

Use this template when assigning a phase:

```text
Read docs/inventory-automation-codex-plan.md as the primary technical implementation plan.
Read docs/google-drive-inventory-automation.md only as the business/operational reference.
Read docs/inventory-system-readiness-assessment.md only for historical context.

Before editing, inspect the current branch because source code always overrides stale documentation.

Implement PHASE <N> only.
Do not implement later phases.
Do not perform unrelated refactors.
Preserve current tenant isolation, Google Drive OAuth model, source-sync behavior, durable processing jobs, content deduplication, processing policy, AI governance, Search V3 behavior and Explorer behavior.

Add/update tests required by the phase and run the most relevant existing test suites.
If the baseline or required tests are red, diagnose that first and do not hide the failure by weakening tests.

At completion:
1. summarize files changed;
2. list migrations/config changes;
3. list tests run and results;
4. state residual risks;
5. update the Implementation Log in docs/inventory-automation-codex-plan.md;
6. stop before the next phase.
```

For the current repository state, the first instruction should be:

```text
Implement PHASE 0 only.
Restore the current baseline CI by addressing the two known API/unit regressions documented in the plan. Do not add Inventory feature code yet.
```

---

# 33. Final target acceptance flow

The feature is not complete until this end-to-end flow passes:

```text
Employee uploads supported image directly to Inventory Inbox
        ↓
source sync discovers it
        ↓
source occurrence is routed to Inventory before generic AI
        ↓
content is downloaded/deduplicated/stored
        ↓
explicit Inventory metadata profile analyzes it
        ↓
AI result is normalized and validated
        ↓
ambiguous data enters review instead of being guessed
        ↓
approved document creates idempotent inventory transactions
        ↓
daily run finalizes correctly
        ↓
Excel can be regenerated from PostgreSQL
        ↓
protected Sheet 4 remains unchanged
        ↓
output/backup is written to Google Drive
        ↓
source photo is archived only when configured and safe
```

Operationally, staff should still perform only:

```text
Ghi số → Chụp ảnh → Upload vào Google Drive Inbox
```

Everything after upload is the system's responsibility, with human review only for uncertain or invalid data.
