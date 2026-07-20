# Durable asset pipeline

## Scope

Step 25 connects the existing registry, job queue, storage, AI metadata,
projection, Elasticsearch v2 and sidecar modules. PostgreSQL remains the source
of truth. Every source item has one tenant-scoped `asset_pipelines` record and a
stable correlation ID propagated in job payloads and structured worker logs.

HTTP ingestion persists references and jobs only. Download URLs and provider
credentials remain in their authoritative records/provider adapters and are not
copied into chained job payloads.

## State machine

The allowed transitions are implemented in `app/modules/pipeline/state.py`.
The happy path is:

`discovered → download_pending → downloading → downloaded|duplicate_detected
→ storage_pending → stored → analysis_pending → analyzing → metadata_ready
→ projection_pending|projection_ready → search_pending → indexed
→ sidecar_pending → completed`.

Stages that are not required by policy can transition to the next valid stage
or `completed`. Failures are explicit: `download_failed`, `storage_failed`,
`analysis_failed`, `projection_failed`, `search_failed`, and `sidecar_failed`.
Each failure records a structured code, safe message and retryability. Recovery
returns to that stage's pending state.

## Transaction and retry rules

- Successful state transition and next-job creation share one SQLAlchemy
  transaction. A crash before commit persists neither; a crash after commit
  leaves a claimable job.
- Job idempotency is derived from pipeline, stage and content/analysis/
  projection identity.
- SHA-256 plus the existing `(tenant_id, content_hash)` constraint remains the
  final deduplication guard. Downstream records retain their uniqueness rules.
- Elasticsearch is updated by asset ID through the v2 write alias. Persisted
  projection version/checksum avoids unchanged writes.
- Sidecar failure is independent and never rolls back analysis or indexing.

## Provider boundary

Download and storage handlers depend on provider-neutral `PipelineDownloadStage`
and `PipelineStorageStage` resources. Production composition must supply a
durable source-credential resolver for Google Drive/SharePoint workers; browser
session access tokens must not be copied into jobs or source metadata. External
URL ingestion continues to use the secure downloader.

## Feature flags and rollback

The pipeline respects unified ingestion, content deduplication, managed storage,
AI single/auto analysis, search projection, Elasticsearch v2 and Drive sidecar
flags. No flag is enabled by this step.

To roll back, disable unified ingestion, drain workers, revert Step 25, then
downgrade `0009_durable_asset_pipeline`. Assets, jobs, storage records, analyses,
projections, index documents and sidecars remain authoritative and intact.
