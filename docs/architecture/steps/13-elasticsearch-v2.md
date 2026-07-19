# Step 13 — Elasticsearch v2 index

The v2 search index is a rebuildable projection. PostgreSQL remains the source
of truth. Physical indices use `<prefix>-v2-<version>` and are accessed through
separate `<prefix>-v2-read` and `<prefix>-v2-write` aliases.

The root mapping is `dynamic: strict`. It contains only identity, display
fields, metadata/profile versions, and the stable Step 12 projection. `facets`
uses `flattened`; `path_values` uses `nested`. `metadata_json` is never indexed.

Bulk writes use `asset_id` as `_id` with `doc_as_upsert`, making retries
idempotent. Read/write alias changes are sent together to `_aliases`; the
previous index names returned by the switch are the explicit rollback target.

No route or existing search flow uses this adapter while
`ELASTICSEARCH_V2_ENABLED=false`.
