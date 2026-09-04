# Accepted Architecture Decisions

## ADR-001 — PostgreSQL source of truth

Status: Accepted

PostgreSQL is authoritative for assets, source relationships, metadata,
processing state and search projections.

## ADR-002 — Content identity

Status: Accepted

Asset content identity is tenant-scoped SHA-256.

## ADR-003 — Dynamic metadata document

Status: Accepted

AI metadata is stored as JSONB and is not represented as fixed database columns.

## ADR-004 — Stable search projection

Status: Accepted

Elasticsearch indexes a versioned projection rather than arbitrary AI metadata keys.

## ADR-005 — Independent providers

Status: Accepted

Source, storage and AI provider abstractions are independent.

## ADR-006 — Asynchronous processing

Status: Accepted

Download, storage, AI analysis and indexing execute in workers, not ingestion HTTP requests.

## ADR-007 - Application identity and source credentials are separate

Status: Accepted

Application sessions identify a CAM user and tenant. External sources bind explicitly
to purpose-scoped OAuth connections. Workers resolve a credential from the source at
runtime, so reconnects do not rewrite queued jobs. Provider account email equality is
not required. A universal cam_session remains intentionally deferred.
