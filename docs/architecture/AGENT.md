# Creative Asset Manager Architecture Instructions

## Mission

Evolve the project into a unified Creative Asset Manager supporting Google Drive, Microsoft SharePoint, future external API sources, managed asset storage, dynamic AI metadata documents, stable Elasticsearch search projections, tenant-scoped content deduplication, and asynchronous idempotent processing.

## Source of truth

- PostgreSQL is authoritative for assets, source relationships, metadata, processing state, and search projections.
- Elasticsearch is a rebuildable search index.
- Google Drive metadata sidecars are exports only.

## Identity

- Source identity is `tenant_id + external_source_id + external_asset_id`.
- Content identity is `tenant_id + SHA-256 content hash`.
- Filename, folder, URL, and modified timestamp are never permanent content identity.

## Metadata and search

- Store AI metadata as a dynamic PostgreSQL JSONB document.
- Do not create one database column per AI field or impose one fixed creative schema.
- Metadata profiles may optionally define validation and search configuration.
- Never dynamically map arbitrary metadata keys into Elasticsearch.
- Build a versioned projection containing only `search_text`, `search_terms`, `normalized_terms`, `phrases`, `numbers`, `facets`, and `path_values`.
- Projection rebuilds must not require another AI call.

Required query semantics:

- `cat`: single keyword.
- `cat mama`: soft AND.
- `cat, mama`: strict AND.
- `"est 2015"`: phrase.
- `cat OR dog`: explicit OR.
- `subject:cat`: qualified field or facet.

## Provider boundaries

- Source, storage, and AI providers are independent.
- Domain and application services do not depend directly on Google Drive, Microsoft Graph, Gemini, OpenAI, storage, or Elasticsearch SDKs.
- SDK and HTTP implementation details belong in provider/infrastructure adapters.

## Implementation-specific architecture documents

Before changing a feature area, read its dedicated architecture/implementation documents in addition to this file and the repository security baseline.

For Public Review Portal and Review Board work, the authoritative documents are:

- `docs/architecture/PUBLIC_REVIEW.md`
- `docs/architecture/PUBLIC_REVIEW_BOARD.md`
- `docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md`
- `docs/plans/PUBLIC_REVIEW_REVIEW_BOARD_PHASE_09.md`
- `docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md`

`PUBLIC_REVIEW_BOARD.md` is an approved extension of the Public Review architecture and supersedes older statements that classify internal owner moderation, annotation activity, resolution tracking, or review statistics as future-only work. `PUBLIC_REVIEW_REVIEW_BOARD_PHASE_09.md` extends the implementation plan with mandatory Phase 9. Public Review work must follow the documented phase boundaries and must not reinterpret public-session, folder-scope, asset/source identity, media-delivery, guest-identity, annotation, resolution, statistics, or rich-text security contracts without first updating and reviewing those documents.

## Implementation rules

- Inspect existing conventions before changing code.
- Prefer small isolated changes and preserve current behavior behind flags.
- Do not rewrite unrelated modules or place business logic in controllers.
- Use domain services, repositories, and workers.
- Externally visible processing is idempotent and reinforced by database constraints.
- Every migration includes rollback or downgrade notes.
- Every feature includes tests.
- Update `ROADMAP.md` and `REVIEW.md` after every completed step.

## Completion report

At the end of each step report files changed, migrations added, behavior introduced, tests and results, feature flags, known risks, rollback method, and next recommended step.
