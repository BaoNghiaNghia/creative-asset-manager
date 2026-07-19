# Search Architecture

## Authority

PostgreSQL stores dynamic metadata documents and versioned search projections.
Elasticsearch is a rebuildable read model and never becomes authoritative.

## Stable projection

Step 12 builds a versioned projection with only:

- search_text
- search_terms
- normalized_terms
- phrases
- numbers
- facets
- path_values

Arbitrary metadata_json keys must never be dynamically mapped into
Elasticsearch. The projection and its version are stored independently so a
new projection can be rebuilt without another AI request.

## Query behavior target

- cat: single keyword
- cat mama: soft AND
- cat, mama: strict AND
- quoted est 2015: phrase
- cat OR dog: explicit OR
- subject:cat: qualified field or facet

Existing search remains unchanged until the v2 rollout flags are enabled.

## Phase 8 projection pipeline

The pipeline is deterministic and runs entirely from PostgreSQL metadata_json:

1. MetadataTraverser extracts safe scalar path/value pairs.
2. MetadataNormalizer creates normalized values, tokens, numbers, and phrases.
3. SearchProjectionBuilder applies profile search configuration and hard limits.
4. SearchProjectionService stores the stable document and version separately.

search_terms contains normalized whole scalar values. normalized_terms contains
token-level terms. phrases retains useful multi-token scalar values. numbers
contains integer-like tokens. path_values provides stable qualified-path lookup.

Profile search_config_json supports include_all_scalar_values, text_paths,
facet_paths or facets, boost_paths, exclude_paths, and include_booleans.
Facet paths are explicit; arbitrary metadata keys never become mappings.
Boosts remain separate query configuration.

SEARCH_PROJECTION_ENABLED remains false. Existing search and Elasticsearch
flows are unchanged until their later rollout steps.
