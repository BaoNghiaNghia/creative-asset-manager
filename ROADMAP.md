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

## Operations and product

- [x] 20 Projection rebuild and reindex
- [ ] 21 UI and admin operations
  - [x] 21A Processing status badges
  - [ ] 21B Asset details
  - [ ] 21C Search syntax
- [x] 22 Controlled rollout
- [x] 23 Worker runtime, health checks, and graceful draining
- [x] 24 Gemini single-asset metadata analysis
- [x] 25 Durable end-to-end asset pipeline
- [x] 26 Tenant-scoped rollout, tenant-filtered claiming, and pause controls
