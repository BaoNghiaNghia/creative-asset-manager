# AI batch operations

## Preconditions

Keep batch processing disabled until all of the following are true:

- migration `0012_ai_batch_processing` is applied;
- Step 27 cost rates and tenant budget policy are configured;
- the tenant processing policy enables the pipeline and AI stage;
- the Gemini provider/model has been staging-tested;
- workers are running with normal lease and drain controls.

Global flags remain emergency upper bounds.

## Controlled enablement

1. Enable `PROCESSING_JOBS_ENABLED`, `UNIFIED_ASSET_INGESTION_ENABLED`,
   `DYNAMIC_AI_METADATA_ENABLED`, and `AI_BATCH_ANALYSIS_ENABLED` for the
   worker deployment.
2. Keep `AI_BATCH_FALLBACK_TO_SINGLE_ENABLED=false`.
3. Enable the tenant pipeline and AI stage for one pilot tenant.
4. Confirm the Gemini provider policy is enabled and not paused.
5. Submit a small explicit candidate set through an `ai_batch_prepare` job.
6. Observe batch/item status, processing jobs, usage records, reservations,
   denials, latency and provider errors.
7. Expand item and byte limits only after cost and memory validation.

## Status interpretation

- `preparing/submitting`: local preparation or external submit in progress.
- `ambiguous`: provider acceptance is unknown; retry submission with the same
  stable key. Do not manually create another provider batch.
- `submitted/running`: poll jobs should be scheduled at the configured
  interval.
- `importing`: result import is resumable from `result_cursor`.
- `partial_failed`: successful items are retained and only eligible items are
  retried.
- `expired/failed`: item reservations are reconciled and retry work is
  retained.
- `cancelled`: no new provider work is submitted; unfinished analyses remain
  retryable for explicit operator action.

## Pause and emergency stop

Use the Step 26 tenant or Gemini-provider pause to stop new claims while active
jobs drain. The Step 27 AI emergency stop and global batch flag override tenant
policy. Do not delete queued jobs. After a policy or budget correction, resume
the tenant/provider and allow retained jobs to become eligible.

## Recovery

- Ambiguous submit: rerun the same submit job; the adapter searches the stable
  display name before creating a batch.
- Poll outage: retain the provider batch ID and retry with backoff.
- Import interruption: rerun import; committed custom IDs are skipped and the
  cursor resumes.
- Missing/invalid item: inspect the item error and run the selective retry job.
- Budget denial: raise/reset policy only after review, then retry blocked items.
- Cancellation: cancellation is best effort; continue polling/importing if the
  provider reports already-completed billable work.

## Rollback

1. Disable `AI_BATCH_ANALYSIS_ENABLED`.
2. Pause tenant AI and drain workers.
3. Export batch, item, usage and reservation audit records.
4. Revert Step 28 application code.
5. Downgrade `0012_ai_batch_processing` to
   `0011_ai_governance_pilot`.

Downgrade deletes only batch orchestration records. Assets, completed analyses,
metadata, projections, usage and budget records remain authoritative.
