# Implementation Roadmap

## Foundation

- [ ] 00 Codebase audit
- [ ] 01 Architecture documents and feature flags
- [x] 02 Provider contracts

## Asset ingestion

- [x] 03 Asset registry
- [x] 04 Content hashing and deduplication
- [x] 05 Processing jobs and outbox
- [x] 06 Incremental source synchronization
- [x] 07 Secure external downloader
- [x] 08 Managed asset storage

## Metadata and search

- [x] 09 Dynamic metadata persistence
- [x] 10 Metadata traverser
- [x] 11 Metadata normalizer
- [x] 12 Search projection builder
- [x] 13 Elasticsearch v2 index
- [x] 14 Search query parser

## AI and APIs

- [x] 15 Single-asset AI analysis
- [ ] 16 Pilot evaluation
- [ ] 17 AI batch processing
- [x] 18 External ingestion API
- [x] 19 Metadata sidecar export
- [x] AI-MULTI-01 Multi-provider AI registry and persisted provider resolution
- [x] AI-MULTI-02 OpenAI single-image Responses API
- [x] AI-MULTI-03 OpenAI Batch API
- [x] AI-MULTI-04 Provider, processing-mode, and model request API
- [x] AI-MULTI-05 Correct single/batch enqueue orchestration and bulk API

## Operations and product

- [x] 20 Projection rebuild and reindex
- [ ] 21 UI and admin operations
  - [x] 21A Processing status badges
  - [x] 21B Asset details
  - [x] 21C Search syntax
- [x] 22 Controlled rollout
- [x] 23 Worker runtime, health checks, and graceful draining
- [x] 24 Gemini single-asset metadata analysis
- [x] 25 Durable end-to-end asset pipeline
- [x] 26 Tenant-scoped rollout, tenant-filtered claiming, and pause controls
- [x] 27 Pilot evaluator, AI cost metrics, and AI budget circuit breaker
- [x] 28 Durable AI batch submission, polling, import, and selective retry

- [x] 29 Asset details UI, Search v2 UI, and authorized async operations

- [x] 30 Persistent encrypted OAuth tokens and distributed sessions

- [x] 31 PostgreSQL and Elasticsearch integration CI plus end-to-end pipeline tests

- [x] 32 Reconciliation generation markers, sensitive URL retention, and cleanup jobs

- [x] 33 Deterministic active analysis, search shadow comparison, and safe index lifecycle
  - [x] 33R1 Active-analysis integrity and deterministic rebuild ordering
  - [x] 33R2 Shadow-search safety, metrics, and reporting remediation
  - [x] 33R3 Elasticsearch lifecycle verification, recovery, and safe cleanup remediation

- [x] AI-MULTI-06 Frontend provider, model and processing-mode selection
- [x] AI-MULTI-07 Multi-provider production governance
- [x] AI-OPS-01 Tenant-scoped AI Operations dashboard APIs
- [x] AI-OPS-02 Safe tenant AI Operations control APIs
- [x] AI-OPS-03 AI Operations dashboard UI, interactions and bounded refresh
- [x] AI-OPS-04 Provider and tenant configuration UI
- [x] AI-OPS-05 Performance, bounded exports and final validation
- [x] AI-OPS-CI-FIX Migration and durable pipeline CI regressions

- [x] 21D Toggleable file details and activity inspector

## Authentication and authorization

- [x] AUTH-01 Application users and external identity persistence
- [x] AUTH-02 Durable tenant membership and active tenant resolution
- [x] AUTH-03 Tenant-scoped roles and permissions
- [x] AUTH-04 Central application principal and permission dependencies
- [x] AUTH-05 OAuth application login, session rotation and tenant switching
- [x] AUTH-06 Tenant membership and role administration APIs
- [x] AUTH-07 Access Management frontend
- [x] AUTH-08 Durable RBAC for AI Operations and related admin routes
- [x] AUTH-09 Authentication/RBAC migration tooling and final validation
- [x] ADMIN-SETUP-SCRIPT Safe interactive first-administrator bootstrap
- [x] AUTH-JIT Secure Google and Microsoft self-signup provisioning

## Deployment

- [x] DEPLOY-COMMITTED-FRONTEND Committed Vite bundle, Docker backend and native Nginx/PostgreSQL deployment

- [x] PROD-DOCKER-01 Immutable Docker backend and production Compose
- [x] PROD-FE-02 Reproducible committed frontend release
- [x] PROD-VPS-03 Native Nginx and VPS deployment/rollback tooling
- [ ] PROD-GATE-04 Production release gate (implemented; production readiness requires a green remote gate for the release SHA)
- [x] GEMINI-MODEL-FAILOVER Per-model Gemini quota-aware image-analysis failover


## Search V3 follow-up

- [x] Durable projection-to-index job handoff and worker Elasticsearch provider registration.
- [x] Versioned V3 search mapping/query foundation with visible text, prefix and fuzzy clauses.
- [ ] Run tenant-scoped V3 backfill and alias activation only after Elasticsearch integration verification in the deployment environment.
- [x] AI Operations Search V3 coverage metrics, explicit audit and repair controls.
- [x] Search V3 metadata text indexing: shared document construction now preserves short OCR terms, safely flattens dynamic metadata, and waits for single-asset Elasticsearch visibility.

## Test reliability

- [x] TEST-ENV Deterministic backend unittest environment isolation
- [x] UX File inspector activity timeline and AI Operations header controls
