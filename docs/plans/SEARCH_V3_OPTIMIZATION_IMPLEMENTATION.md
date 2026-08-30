# Search V3 optimization implementation note

## Current behavior

- Search V3 is tenant-scoped and viewer requests fail closed unless an external source is selected.
  Elasticsearch filters preserve source and ancestor-folder restrictions for both hits and facets.
- Selected facet values use OR inside each facet and AND across facets. Aggregations are
  self-excluding: each facet keeps text/security/core filters and all other selected facets.
  Explicit selected-value filters preserve selected values that fall outside the top 50 terms.
- Blank text is valid with any facet, design, source or typed filter; a request with no effective
  condition remains invalid. Filter-only queries use Elasticsearch bool.filter, never match_none.
- Query clauses use bounded relevance tiers (dis_max for equivalent lexical evidence) and fuzzy
  matching is low-weight, bounded and disabled for short tokens. Qualified text clauses use
  match/match_phrase, not term against analyzed fields.
- Cursor V2 binds search_after values to a tenant-aware effective-request fingerprint and PIT.
  V1 is intentionally rejected. Offset is capped at 500 and interactive paging uses PIT.
- Typed V3 fields now include media/mime/extension/provider, source dates, dimensions/duration,
  file size, visible/AI flags and first-class design_type. Newest/oldest/name sort modes all
  include deterministic asset_id tie-breaking.
- Design Type filters directly use terms design_type; provider-only filtering uses the direct
  indexed provider field. External-source filtering and viewer authorization still use
  authoritative source IDs.
- Tenant metadata-profile query configuration uses a bounded revision-keyed local cache.
- A source deletion now enqueues an idempotent search_index_sync worker job after the source
  transaction commits. The job reindexes the asset to a remaining live deterministic source or
  removes its document when none remains. Full-source reconciliation enqueues the same repair.
  Hydration continues as defense in depth and logs stale-hit drops as observable drift.
- Multiple live source links still use one document per internal asset and deterministic
  first-live-source projection. This invariant remains intentionally unchanged.

## Files to change

- Search API/schema/query code:
  `schema.py`, `query_parser.py`, `query_builder.py`, `router.py`, `runtime.py`.
- Elasticsearch abstraction and mapping:
  `elasticsearch_v2.py`, `index_types.py`, `source_index.py`,
  `index_lifecycle.py`.
- Existing indexing callers in pipeline and search operations to pass typed source details.
- Focused Search V3 unit/API/lifecycle/infrastructure tests and frontend request/response
  contracts where the API adds sort/total-relation fields.
- Consistency observability in the existing hydration/indexing paths; no new message bus.

## Migration/index impact

Phase 1 query/facet/cache changes are deployable against the current V3 index.
Cursor V2 and PIT are API/runtime changes and emit only V2 cursors while explicitly
rejecting legacy V1 cursors.

Typed fields change the strict Elasticsearch mapping. They require a new physical V3 index,
a full idempotent rebuild, lifecycle verification, and atomic read/write alias activation.
The active index must not be mutated in place.

## Compatibility risks

- Completely empty requests remain invalid; blank text is accepted only when at least one
  effective filter exists.
- Legacy cursor V1 is intentionally rejected because it cannot be validated against request
  context or an index snapshot. The current frontend treats cursors as opaque and is
  compatible with V2.
- New sort modes require the new typed index. Relevance remains the default and is supported
  during the transition; typed sorts fail clearly if governance says the index is
  incompatible.
- Multi-source modeling is not changed in this patch. Deterministic first-source projection
  remains a documented limitation and a separate source-domain decision.
- PIT availability depends on the deployed Elasticsearch version. Failure is surfaced as a
  retryable search error; no authorization filter is relaxed.

## Test plan

- Query/parser: exact, phrase, lexical, prefix, fuzzy fallback, short-token fuzzy guard,
  qualified analyzed fields, AND/OR modes and clause fan-out bounds.
- Facets/filter-only: same-facet OR, cross-facet AND, self-exclusion, selected-value
  preservation, source/design/typed-only requests and empty-request rejection.
- Security: tenant, viewer source/folder filters remain in hits and every facet aggregation.
- Pagination/sort: cursor V2 round-trip/fingerprint mismatch, malformed/version errors,
  offset conflict/cap, PIT open/search/close behavior and deterministic sort values.
- Mapping/projection/lifecycle: typed fields, design type, source provider, required mapping,
  new physical index verification and alias rollback.
- Consistency: idempotent upsert/delete provider calls, hydration-drop observability and
  reconciliation fixtures.
- Run all Search V3 tests plus frontend Search V3 contract/pagination tests and production
  builds.
