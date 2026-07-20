# Step 29  Asset details and Search v2 UI

Status: Completed

## Asset details read model

`GET /api/v1/assets/{asset_id}` is tenant-scoped and returns bounded,
paginated PostgreSQL projections for identity, source records, managed storage,
AI analysis history, metadata, search projection, pipeline state and processing
jobs. URLs are stripped of query strings. Provider credentials, raw responses
and signed URLs are not exposed. Cost/usage is visible only to processing
administrators.

The explorer opens a responsive right-side details panel from Search v2 results.
The internal `asset_id`, `external_source_id` and provider external asset ID
remain separate. The metadata/projection tree has depth and node limits.

## Operator actions

`POST /api/v1/admin/assets/{asset_id}/actions` uses existing processing-admin
authorization. Reanalysis, forced reanalysis, projection rebuild, reindex and
failed-stage retry enqueue normal jobs. Forced analysis requires an explicit
confirmation. Queued/retry jobs may be cancelled without modifying running jobs.

## Search v2 rollout

`GET /api/v1/search/capabilities` selects v2 only when global Elasticsearch,
query-parser and tenant search policy gates are effective and Elasticsearch is
configured. Otherwise the client retains the existing explorer search.

`POST /api/v1/search` applies tenant and selected-source filters before
Elasticsearch execution, accepts only configured profile facets, returns
composite source identity, and includes parsed-query debug only for privileged
operators. Query and facet state is stored in the URL.

Supported examples: `cat`, `cat mama`, `cat, est, 2015`, `"est 2015"`,
`cat OR dog`, `subject:cat`, and `text:"mama"`.

## Rollback

Disable `ELASTICSEARCH_V2_ENABLED` or the tenant `search_v2_enabled` policy
to return the UI to v1 immediately. Revert the Step 29 application commit to
remove the panel and endpoints. No database migration or data rollback is
required.
