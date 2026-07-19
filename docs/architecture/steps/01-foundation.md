# Step 01 — Architecture Documents and Feature Flags

## Objective

Create the architecture foundation and centralized rollout flags without enabling or implementing any target runtime path.

## Deliverables

- Repository-local architecture instructions and accepted ADRs.
- `CURRENT_STATE.md` and `IMPLEMENTATION_PLAN.md` from Step 00.
- Root `ROADMAP.md` and `REVIEW.md` status documents.
- A typed API settings service containing all Step 01 flags.
- Strict boolean validation and configuration tests.
- `.env.example` entries with every flag set to `false`.

## Constraints

- No route, database migration, worker, provider flow, or UI behavior is added.
- All new flags default to `false` and no application branch consumes them.
- FastAPI startup validates configured flag values centrally.
- Only literal `true` or `false` values are accepted, case-insensitively.
- Later steps must use the central config service instead of introducing scattered environment reads for these flags.

## Feature flags

The authoritative list is defined in `apps/api/app/core/config.py` and documented in `.env.example`:

- `UNIFIED_ASSET_INGESTION_ENABLED`
- `CONTENT_DEDUP_ENABLED`
- `INCREMENTAL_SOURCE_SYNC_ENABLED`
- `PROCESSING_JOBS_ENABLED`
- `EXTERNAL_ASSET_DOWNLOADER_ENABLED`
- `MANAGED_ASSET_STORAGE_ENABLED`
- `DYNAMIC_AI_METADATA_ENABLED`
- `AI_SINGLE_ANALYSIS_ENABLED`
- `AI_BATCH_ANALYSIS_ENABLED`
- `AI_AUTO_ANALYZE_ENABLED`
- `SEARCH_PROJECTION_ENABLED`
- `ELASTICSEARCH_V2_ENABLED`
- `SEARCH_QUERY_PARSER_V2_ENABLED`
- `EXTERNAL_INGESTION_API_ENABLED`
- `DRIVE_METADATA_SIDECAR_ENABLED`

## Acceptance criteria

- The application starts and exposes exactly the same routes as before.
- There are no migrations or active worker changes.
- Every flag defaults to false.
- Explicit true/false values are accepted; invalid values fail validation.
- Configuration tests run in CI.
