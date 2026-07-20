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

## Phase 9 Elasticsearch v2

Physical indices are versioned as `<prefix>-v2-<version>`. Applications read
through `<prefix>-v2-read` and write through `<prefix>-v2-write`. Both aliases
are switched in one aliases API request, and a switch returns the prior targets
for explicit rollback.

The v2 mapping uses `dynamic: strict` and has only these stable fields:

- asset_id and tenant_id
- filename and folder_path
- search_text, search_terms, normalized_terms, phrases, and numbers
- flattened facets
- nested path_values with path and value
- metadata_profile, metadata_profile_version, and search_projection_version

metadata_json is deliberately absent. Bulk updates use asset_id as the
Elasticsearch document ID and doc-as-upsert semantics.

## Phase 9 query language

The provider-neutral parser supports:

- one term as a single keyword query
- space-separated terms as soft AND
- comma-separated clauses as strict AND
- quoted phrases
- explicit, case-insensitive OR
- qualified terms and phrases

Qualified names are allowlisted through stable field aliases, configured
facets, or configured path_values. Invalid syntax and unknown qualifiers fall
back to normalized plain-text clauses without using user input as a field name.
Every generated query includes a tenant_id filter.

Boost precedence is exact number, exact phrase, exact search term, configured
path, normalized term, filename, search_text, then folder path. Index-time
projection and query parsing both reuse MetadataNormalizer.

ELASTICSEARCH_V2_ENABLED and SEARCH_QUERY_PARSER_V2_ENABLED remain false. The
v1 search path is unchanged and no new route or worker is registered.
## Phase 12 rebuild and reindex operations

Projection rebuilds page completed PostgreSQL analyses and reuse the deterministic
SearchProjectionBuilder. Selection is tenant-scoped and can be narrowed by
profile, current projection version, explicit assets, missing projections, or a
prior run's failed items. Checkpoints and cancellation state are committed after
each bounded page.

Reindex creates a new versioned physical index and bulk-upserts directly into
that target. Read/write aliases switch atomically only after the run has no
failed items and no cancellation. Dry-run never mutates projections,
Elasticsearch indices, or aliases. No operation invokes AI.


## Step 29 client contract

The client discovers the effective search version through
`GET /api/v1/search/capabilities`. Query and configured facet selections use
URL parameters (`q` and `facet.<name>`). V2 results always carry
`asset_id`, `external_source_id`, provider and provider external asset ID; the
UI must not collapse those identities. Parsed query debug is administrator-only.
If v2 is disabled globally or for the tenant, the existing explorer search stays
active.

## Step 33 search governance

Search indexing uses an explicit active-analysis pointer scoped by tenant,
asset, metadata profile and search context. Activation locks the authoritative
asset row, retains analysis history, and emits an append-only audit. With the
deterministic-analysis flag enabled, workers reject jobs whose explicit
analysis does not match that pointer; rebuild queries join the active pointer.

Shadow comparison is tenant configured but globally bounded. The primary result
returns immediately; a sampled background call has a strict timeout and cannot
affect the response. Observations store a query hash, bounded features,
versions, latencies, counts, top-k/rank agreement and an error class. Raw query
retention is opt-in and sensitive-looking queries are never stored.

Index lifecycle states are building, validating, active, previous, retired,
deletion_pending, deleted and failed. Activation requires mapping, count,
failure, projection-version, fixture and tenant-isolation verification. A
lifecycle-enabled rebuild never switches aliases automatically. Cleanup
preserves active aliases and configured previous versions, requires minimum age
and confirmation, and rechecks cluster aliases immediately before deletion.
