# Step 28  AI batch processing

Status: Complete

## Scope

Step 28 adds asynchronous AI batch preparation, submission, polling, result
import and selective retry. It reuses the Step 24 analysis pipeline and Step 27
budget accounting. It does not add UI, OAuth, or automatic enablement.

## Provider boundary

`AiMetadataProvider` exposes provider-neutral batch capabilities:

- `supports_batch`
- `submit_batch`
- `get_batch_status`
- `stream_batch_results`
- `cancel_batch`

The Gemini REST adapter owns Google request/response shapes. Domain services
never import a Google SDK. Providers without batch support fail closed.

## Durable flow

1. `ai_batch_prepare` selects tenant-scoped pending analyses.
2. Compatible candidates are grouped by provider, model, metadata profile and
   version, prompt version, pipeline version, and input media family.
3. `ai_batch_submit` reserves budget per item before preparing a bounded,
   permission-restricted temporary JSONL file.
4. A stable submission key and display name recover ambiguous Gemini submits.
5. `ai_batch_poll` follows provider state and retry guidance without tight
   polling.
6. `ai_batch_import` imports results by stable custom item ID, commits a
   cursor after every result, and reuses the single-analysis validator and
   projection builder.
7. `ai_batch_retry_items` retries only eligible failed, missing, or
   budget-blocked items. Single-item fallback is separately flagged and off.

Queued work contains database identifiers only. Prompts and image bytes are
held in a temporary file with mode 0600 and removed on every exit path.

## Idempotency and failure handling

- `(tenant_id, submission_key)` prevents duplicate logical batches.
- `(tenant_id, analysis_id)` prevents one analysis joining multiple batches.
- Provider batch IDs are unique per provider.
- Repeated submission recovers by stable provider display name.
- Result import ignores duplicates, records bounded unknown IDs, and checkpoints
  after each item.
- Provider-level failure reconciles reservations and creates selective retry
  work. Ambiguous external outcomes retain reservations until recovery.
- Batch provider cost is allocated deterministically to item usage records and
  reconciles existing estimates without double counting.

## Feature flags

`AI_BATCH_ANALYSIS_ENABLED=false` gates all five batch worker job types.
`AI_BATCH_FALLBACK_TO_SINGLE_ENABLED=false` independently gates fallback.
The existing global processing, unified ingestion, dynamic metadata, tenant AI,
pause, provider and concurrency gates still apply.
