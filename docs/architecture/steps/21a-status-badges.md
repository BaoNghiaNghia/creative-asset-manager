# Step 21A — Asset processing status badges

The existing asset grid displays a compact, accessible lifecycle badge without
redesigning the explorer. The metadata query response includes one derived
`processing_status` for every requested source item:

- `discovered`
- `stored`
- `analyzing`
- `metadata_ready`
- `indexed`
- `duplicate`
- `failed`

The status projection is read-only and tenant/provider scoped. PostgreSQL
registry, source links, storage objects, latest analysis, latest relevant job,
and completed reindex operation records remain authoritative. No new status
column or migration is introduced.

Precedence is `failed`, `indexed`, `metadata_ready`, `analyzing`,
`duplicate`, `stored`, then `discovered`. This prevents an old lifecycle
stage from hiding a later failure or a completed search index state.
