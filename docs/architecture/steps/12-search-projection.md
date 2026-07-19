# Step 12 — Search projection builder

## Stable document

SearchProjectionBuilder emits only search_text, search_terms,
normalized_terms, phrases, numbers, facets, and path_values. Projection version
is returned and persisted separately from this document. Boost paths remain
query configuration rather than Elasticsearch fields.

search_terms contains unique normalized scalar values. normalized_terms
contains unique token-level values. phrases contains useful normalized
multi-token values, and numbers contains integer-like tokens.

## Profile search configuration

- include_all_scalar_values controls whether every safe scalar participates.
- text_paths select searchable subtrees when include-all is disabled.
- facet_paths or facets configure explicit, stable facet names and paths.
- boost_paths is normalized into separate query configuration.
- exclude_paths adds profile-specific traversal exclusions.
- include_booleans opts into boolean extraction.

All paths use index-free logical path matching. Facets are never created from
arbitrary metadata keys.

## Rebuild

SearchProjectionService reads stored metadata_json and profile
search_config_json, invokes only the traverser, normalizer, and builder, then
stores search_projection and search_projection_version separately. It imports
no AI provider and is disabled by default behind SEARCH_PROJECTION_ENABLED.

This step adds no migration, route, worker, Elasticsearch mapping, or AI call.
