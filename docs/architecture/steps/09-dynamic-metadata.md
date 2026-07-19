# Step 09 — Dynamic metadata persistence

## Scope

This step persists metadata profiles and AI analysis history. It does not call
an AI provider, register a route, or start a worker.
DYNAMIC_AI_METADATA_ENABLED remains false.

## Metadata profiles

metadata_profiles versions the prompt template, optional JSON Schema, search
configuration, and active state per tenant. Schema validation happens when a
profile is created and when an analysis result is completed.

## Analysis history

asset_ai_analyses snapshots the asset content hash, profile name/version,
prompt version, pipeline version, provider/model identity, state, errors and
timestamps. Dynamic metadata, optional raw response, and stable search
projection are separate JSONB documents.

Normal analysis creation is idempotent for tenant, asset, content hash, profile,
prompt version, and pipeline version. A partial unique database index is the
final concurrency guard. Forced re-analysis creates a new history row and never
overwrites a completed analysis. Raw responses are stored only when explicitly
enabled by the caller.

## Rollback

Export required history, disable AI/metadata flags, then downgrade revision
0004 to 0003. Legacy tag/rating metadata is unaffected.
