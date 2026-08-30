# Search V3 optimization implementation note

## Current behavior

- Search V3 is the only runtime generation and all hit queries are tenant-scoped.
- Viewer requests fail closed unless an external source is selected; Elasticsearch filters
  preserve both source and ancestor-folder restrictions.
- Facet filters use OR within a facet and AND across facets for hits, but aggregations are
  calculated after every selected facet filter, so counts are not self-excluding.
- The request requires non-empty text. Parsing blank text yields no clauses and the builder
  emits `match_none`, preventing legitimate filter-only discovery.
- One term fans out across exact, phrase, prefix, fuzzy and repeated lexical fields inside
  one additive `bool.should`, which can stack duplicate evidence.
- Qualified analyzed text terms can emit `term` queries; only qualified phrases use
  `match_phrase`.
- Cursor V1 stores only score and asset ID. It is not bound to query/filter/sort context and
  searches the read alias without PIT stability.
- Offset has no product-level deep-page cap. The current frontend already uses cursor
  pagination after page one.
- The V3 projection contains source ID and folder ancestry but no typed media/date/size,
  source-provider, or first-class design-type fields.
- Design Type currently matches any `path_values.value` without constraining its metadata
  path.
- Source-provider filters resolve source IDs in PostgreSQL before the Elasticsearch query.
- Tenant search configuration loads all active metadata profiles on every request.
- Hydration defensively removes stale hits but drift is not explicitly measured.
- Multiple live source links are supported by the schema; indexing intentionally chooses
  the deterministic first live source and uses one document per internal asset.

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
