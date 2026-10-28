# Implementation Roadmap

## GatorHats 8869 ZIP photo synchronization — 2026-10-24

- [x] Parse official color/angle tokens from ZIP filenames and directory names, with bounded image validation.
- [x] Preview complete, incomplete, duplicated and unmatched photo groups before import.
- [x] Store complete stock sets in immutable versions outside the release checkout and publish manifest atomically.
- [x] Wire versioned references into the Embroidery Design 2x2 gallery, Ecommerce image inputs and future 13-color jobs without reprocessing historical outputs.
- [ ] Verify the user's original 8869 ZIP name matches and execute the first live image sync after importing its bytes.

See [GatorHats stock sync](docs/operations/GATORHATS_HAT_STOCK_SYNC.md).

## Job-growth prevention — Source Sync 3-minute cooldown (2026-10-23)

- [x] Trace 28,186 scheduled source sync jobs over seven days to the 60-second poll loop (3 sources).
- [x] Add an indexed, DB-read-only 180s cooldown after successful incremental syncs, with no migration.
- [x] Exempt first synchronization, manual/full, daily full, cursor-bootstrap, failure/retry and continuation jobs.
- [ ] Monitor steady-state source update latency and source_sync job count in production before tuning the interval.

## Disk pressure — phase 2 fail-closed production rollout (2026-10-23)

- [x] Keep legacy job identities/idempotency intact; inspect live FK/index dependencies.
- [x] Add private remote-backup receipt published only after checksum-verified workflow success; never equate with tested restore.
- [x] Refuse to schedule/execute retention in production without >=8GiB free and a recent remote backup receipt; keep flag OFF by default.
- [x] Include fixed read-only disk-watch service/timer in the reviewed backend deployment workflow.
- [ ] Verify isolated restore of recent production dump, disk >=8GiB and activate the bounded retention canary.
- [ ] Review job-history tombstone/archival proposal and controlled index rebuild separately; do not mass-delete 646k jobs.

## Disk pressure P0/P1/P2 safeguards — 2026-10-22

- [x] Add opt-in tenant retention canary for completed job payloads only, without deleting job identities or changing the default disabled production policy.
- [x] Recover from partially created cleanup runs if queue enqueue fails; protect job idempotency and keep errors redacted.
- [x] Harden Codex/video proxy temporary cleanup observability and reject unsafe attempt paths.
- [x] Add retry for transient Google Drive *reads* in legacy Stage 1 retention; never retry deletion.
- [x] Add lightweight disk threshold monitor and optional systemd timer (source only, not automatically installed).
- [ ] Approve and activate low-rate retention canary after validated backups/free-space, then verify count-only metrics.
- [ ] Design a separate reviewed PostgreSQL job-history archival/tombstone migration; never mass-delete existing job rows.
- [ ] Schedule standalone review of orphaned video-final checkpoints, safely mapped to committed remote objects.

Runbook: [Disk pressure and retention operations](docs/operations/DISK_PRESSURE_RETENTION_20261022.md).

## Inventory business audit (2026-10-21)

- [x] Make stale source/readback conflicts terminal for human review; preserve technical retries and active leases.
- [x] Block the legacy Catinat automatic/manual carry-forward writer using exact Q47 configuration/source/target worksheet identities.
- [x] Add manager-authorized, evidence-bound atomic material bulk review and protect approval audit metadata against stale candidate/evidence updates.
- [x] Correct UI authorization/request lifecycle and add staff/manager fixtures and targeted regressions.
- [x] Record Inventory API 820 passed, frontend 814 passed across 146 files, analyzer 86 passed, typecheck/build PASS.
- [x] Pass all 30 compiled strict/full staff/manager browser states on phone, tablet, and desktop, with zero recorded issues and zero viewport overflow.
- [x] Correct and verify click-open / Enter-close disclosure capture in the Overview QA plan; preserve tested runtime source and dist (targeted planner tests 14/14).
- [x] Prepare governed 15-image Overview proposal `20261021T150736Z-b1a9bc23-inventory` from the actual reviewed change, with zero browser/runtime issues and preserved legacy profile coverage.
- [x] Record explicit user authorization and complete normal baseline acceptance for `20261021T150736Z-b1a9bc23-inventory` (2026-10-21 15:23:18 UTC / 22:23:18 Vietnam): strict/baseline-accept **15/15 matches under unchanged tolerances**, zero runtime/step/HTTP/unclassified request errors, and 3 classified identity aborts. Preserve exact reviewed hashes for all 15 accepted PNGs and all 12 legacy PNGs; acceptance commit A `3a2dc90091bca52e313c319c20bdbd3e5b406081` is recorded.
- [ ] Establish live backend provenance/schema compatibility and record real PostgreSQL concurrency validation limits.
- [x] Record historical source S `955af39383ba80ce0680f428c786cb1191aef8b9`, its exact-source build, and artifact R `d06995e9f435904e2cdec9ed1189c3b34164dad9` with verification evidence; baseline acceptance does not change the tested runtime.
- [ ] Restore GitHub HTTPS write authentication on the CodeLocal host, publish the review branch, obtain actual CI, and follow the reviewed clean-checkout release workflow after the remaining gates; push is blocked and no deployment has occurred.

Details: [Inventory business audit — 21 October 2026](docs/operations/INVENTORY_BUSINESS_AUDIT_20261021.md).

## Browser Agent recovery candidate 0.2.12 / Manager 0.1.43 (2026-10-21)

- [x] Reproduce and repair parser counts, successful-prefix loss, serial-batch continuation and same-tab/popup refusal handling.
- [x] Preserve immutable reports on cleanup failure and retain uncertain profile ownership for operator review.
- [x] Serialize standby Start/Stop/retry/update decisions; verify six isolated Rust lifecycle-policy tests.
- [x] Verify all 93 worker tests (including 20 recovery regressions), three release-builder tests, extracted-ZIP smoke, Manager frontend tests/build, version preflight and scoped staging gate.
- [x] Unify package/report/heartbeat provenance and correct Start-versus-browse instructions.
- [ ] Complete native Windows/Tauri build and real approved browser acceptance; do not equate source or heartbeat with deployed scanning.
- [ ] Deploy the verified candidate through the existing Windows release workflow; production scan dispatch remains disabled.

## Branding DNA Windows runtime (2026-10-20)

- [x] Implement paired live CAM heartbeat launcher with read-only shop/job diagnostics and no duplicate local shop configuration.
- [x] Add unit tests for token hygiene, real CAM protocol shape, and fail-closed dispatch enforcement.
- [ ] Start and verify persistent launcher on `DESKTOP-3FR0LF0` when CodeLocal reconnects.
- [ ] Verify marketplace-specific authorizations, source rights and shop activation one-by-one.
- [ ] Add independently reviewed worker lease/dispatch protocol before any unattended Amazon/Etsy crawling.


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
- [x] PIPELINE-OPS Tenant-scoped end-to-end Google Drive processing operations overview
- [x] Pipeline Overview compact operational layout (grouped summary/context cards)

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
- [x] GEMINI-RPD-RESET Durable Gemini RPD quota-ID recognition and Pacific reset deferral


## Search V3 follow-up

- [x] Durable projection-to-index job handoff and worker Elasticsearch provider registration.
- [x] Versioned V3 search mapping/query foundation with visible text, prefix and fuzzy clauses.
- [ ] Run tenant-scoped V3 backfill and alias activation only after Elasticsearch integration verification in the deployment environment.
- [x] AI Operations Search V3 coverage metrics, explicit audit and repair controls.
- [x] Search V3 metadata text indexing: shared document construction now preserves short OCR terms, safely flattens dynamic metadata, and waits for single-asset Elasticsearch visibility.
- [x] Search V3 repair retries: failed terminal asset_index jobs create one fresh, audit-preserving index job keyed by the failed job ID.

## Test reliability

- [x] TEST-ENV Deterministic backend unittest environment isolation
- [x] CI API suite aligned with durable Search V3 projection-to-index handoff
- [x] WORKER-ELASTICSEARCH-LOOP Persistent worker event loop for sequential asset indexing
- [x] PIPELINE-RECOVERY Durable Google source-sync/download recovery, fresh failed-download repair jobs, and worker-owned async stage execution
- [x] UX File inspector activity timeline and AI Operations header controls
- [x] AI-OPS dashboard response mapping correctness
- [x] AI-OPS responsive filter toolbar refinement

- Pipeline Overview logical-asset aggregation: completed — current dashboard state is deduplicated by active source asset; raw processing attempts are diagnostics only.

- [x] UX Search guide sizing and accessible input-border loading feedback
- [x] UX Search cleared-query loading-state reset
- [x] UX Search-result thumbnail viewport lazy loading with loading skeletons
- [x] UX Search-result grid skeleton while a modern search request is pending


- [x] Viewer folder scopes: tenant administrators can select external Drive
  folders for viewer memberships; Explorer and search enforce the scope.

## Public Review Portal

- [x] PUBLIC-REVIEW-01 Durable share, scope, guest, session, and annotation foundation.
- [x] PUBLIC-REVIEW-02 Server-side public-share principal and fail-closed folder-scope authorization.
- [x] PUBLIC-REVIEW-03 Authenticated share-management API with tenant RBAC, scope validation, rotate/revoke, and secret-free audit.
- [x] PUBLIC-REVIEW-04 Scoped bearer-link read API, durable public rate limits, and CAM-side media delivery.
- [x] PUBLIC-REVIEW-05 Anonymous session guest annotation backend with ownership, rich-text validation, CSRF and durable write limits.
- [x] PUBLIC-REVIEW-06 Public Review frontend portal with bearer-link bootstrap, scoped browse/search/view, annotation interactions, and no mandatory reviewer identity prompt.
- [x] PUBLIC-REVIEW-07 Tiptap rich annotations, safe JSON rendering, and normalized image pins.
- [x] PUBLIC-REVIEW-08 Asset Explorer share-management integration, one-time link handling, rotate/revoke controls, and final Public Review hardening.
- [x] PUBLIC-REVIEW-09 Internal Review Board with tenant-scoped read/stat APIs, resolution workflow, audit, authenticated UI, and production-readiness hardening.

## Cloudflare R2 original-video cache

- [x] Phase 1 private R2 adapter, bounded multipart SHA-256/size verification, tenant-scoped cache metadata, and migration 0082 (local only).
- [x] Phase 2 durable video-only cache-fill jobs, serialized global reservations/quota, LRU eviction, retry, cleanup and reconciliation (local only; disabled by default).
- [x] Phase 3A short-lived signed video URLs and private Worker GET/HEAD foundation (local only).
- [x] Phase 3B Range/206 and Cloudflare edge caching (implemented locally; not deployed).
- [x] Phase 4A persisted global `VIDEO_CDN_DELIVERY_ENABLED` runtime gate and platform-admin UI (local only; default OFF; no playback change).
- [x] Phase 4B Public Review playback integration with exact asset/source authorization, short signed-ticket redirect, session-expiry cap, and provider source fallback (code complete; runtime remains default OFF).
- [x] Phase 4C fail-closed rollout-readiness preflight, signed missing-key Worker probe, and Worker test/typecheck CI coverage (no production deployment; runtime remains OFF).
- [x] Phase 4D tenant-scoped CDN canary gate, HEAD-only READY-object probe, secret-free Wrangler production config renderer, and non-executing Worker rollout planner (no production deployment; runtime remains OFF).
- [x] Phase 4E process-local delivery observability and circuit breaker: decision/fallback/probe metrics, bounded latency samples, admin-safe snapshot, debounced signed HEAD health sampling, and automatic provider fallback without mutating the persisted master toggle.
- [x] Phase 5A production activation hardening: Phase 4E guard is a master-toggle prerequisite, READY preflight is no-redirect, and one read-only activation command combines production/config/DB/quota/canary plus missing-key and READY-object Worker probes. Production runtime remains OFF until an operator explicitly enables it.
- [x] Phase 5B Platform Admin activation console: frontend schema alignment for Phase 4D/4E, rollout/prerequisite visibility, safe process-local redirect/fallback/probe/latency and circuit state, plus rollback access even if observability is unavailable. No production activation.


## Realistic Review UGC

- [x] RRUGC-PINTEREST-AUTO-SCOUT-V2 Persistent tenant Scout Agent pairing, durable campaign leases/scheduling, repeated bounded Pinterest pulls, Pin-level dedupe, automatic analysis/import handoff, login/challenge pause-and-resume, and production UI controls.
- [x] RRUGC-PINTEREST-SCOUT-V40: repair Pin-detail timeout/Pin-link-only HTTP 422 batch failures; bound alt/query lengths; support active older Scouts through auto-agent-only sanitized API requests, without changing strict manual ingestion.
- [ ] Follow-up operational verification: restart Review + Keyword CMD launchers on every Windows Scout machine; confirm both report v42 and no new 422 in server logs.
- [x] RRUGC-STAGE0-FAVORITES-TAILS: persist independent Favorites next to Pick/Used; add favorite-only and server-side Short-tail (2 words), Mid-tail (3–4 words), and Long-tail (5+ words) filters with correct total/pagination; add Alembic 0137 and tenant-scoped API regression tests.
- [x] RRUGC-STAGE0-SEARCH-ASSIST: remove the page-only volume segmented filter; provide tenant-scoped keyword autocomplete/suggestions and account+tenant-isolated browser search history with keyboard navigation, recent removal and clear-history controls.
- [x] RRUGC-SCOUT-V42: reduce Review empty-claim polling during Gemini backpressure; preflight Keyword capacity before Pinterest detail expansion; commit root/deep expansion history only after child processing succeeds so capacity pauses remain retry-safe.
- [x] EXPLORER-CREATE-FOLDER-ALL-USERS: expose Create folder to every authenticated Google Drive user, retain tenant/source/write-scope and Viewer folder-scope enforcement, and refine the Folder+ action icon for context-menu clarity.
- [x] RRUGC-STAGE0-TAIL-KPIS: replace Monthly Volume / High Competition / Zero Volume KPI cards with server-counted Short-tail / Mid-tail / Long-tail cards, reuse word-count icons in filters/actions, and make the master All action clear search, usage, favorite and tail filters.
- [x] AI-OPS-PROVIDER-KEY-POOLS: compact Providers UI; replace fixed 10-slot Image/Video backup pools with dynamic positive slots; add an independently permissioned Inventory backup pool with round-robin resolution and migration 0138.
- [x] RRUGC-STAGE1-COLORWAY-UI: insert a new Stage 1 between keyword analysis and Pinterest discovery; filter source plans by `embroidery_` filename prefix; design the skill selector, 13-color batch strip, progress/status model and responsive workspace; renumber existing user-facing stages to 2/3/4.
- [ ] RRUGC-STAGE2-COLORWAY-RUNNER-RELEASE: implementation now includes the 13-color stock manifest mapping, tenant-scoped durable per-revision/per-color queue, specialized Scale Image Skill, worker, private output endpoint, retries, and existing Stage 2 UI progress (migration 0143). Backend unit/integration-stub tests and Stage 2 feature-state browser QA passed; still awaiting authenticated real-provider end-to-end output verification and explicitly authorized production release. The separate generic visual baseline mismatch remains unaccepted. See `docs/operations/RRUGC_COLORWAY_RUNNER.md`.
- [x] EXPLORER-MOVE-ALL-USERS: allow every authenticated Google Drive user to move files/folders within their authorized source using drag/drop or Ctrl+X/Ctrl+V; preserve Drive write scope, tenant/source boundaries, Viewer folder scope, cycle protection, local source-parent consistency and cache invalidation.

- [x] RRUGC-SIX-STAGE-HARDENING-1: Stage 1 standalone Keyword Skill; Stage 2 Codex executable preflight; Stage 4 scoped cancellation and sequential 3-reference queue; Stage 5 incomplete-only 500-item analysis drain, conservative quality gate and synthetic disclosure. No migration. See docs/operations/RRUGC_STAGE_HARDENING.md.
- [ ] RRUGC-SIX-STAGE-HARDENING-2: Stage 0 keyword provenance and short-token policy, Stage 3 calibrated prefilter and Gemini throughput, Stage 2 output revision history/QA and authenticated canary, Stage 5 gallery pagination.

- [x] CAM-RESEARCH-SYNC-HARDENING: DNS-pinned TLS public RSS collection; no unacknowledged sync, concurrent PostgreSQL ON CONFLICT dedup, complete feed batch ingestion, bounded operator-triggered `research-cycle`. See `docs/operations/CAM_AI_QA_RESEARCH_INTEGRATION.md`.
- [x] RRUGC-MARKET-INTELLIGENCE-PHASE0: canonical Pinterest/Etsy/Amazon listing links accepted into *unverified* read-only Research Inbox without API calls, paid volume requests, migrations or new secrets; domain spoofing rejected, same listing query parameters deduplicated.
- [ ] RRUGC-MARKET-INTELLIGENCE-PHASE1/2: additive marketplace evidence graph + authorized Etsy/Amazon connectors, rate-limit budgets, normalized quote clustering and unified Stage 0 platform filter; access and licensing required before activation. See `docs/architecture/RRUGC_MULTI_PLATFORM_MARKET_INTELLIGENCE.md`.
- [x] RRUGC-STAGE-TAB-COMPACT-PLATFORM: GatorHats Products/Reviews Stage tabs use intrinsic widths rather than 3 full-width sections; active state returns to standard blue underline with no yellow gradient; Stage 0 Platform column is content-sized immediately before Created date. Desktop/tablet/mobile fixture QA passed. No API or database change.
- [x] RRUGC-ALWAYS-ON-DISCOVERY-V1: independent Exa MCP VPS hourly worker with quota/cooldown and SQLite outbox; existing paired-agent tenant-scoped import; read-only Windows Manager v0.1.18 VPS status, best-effort graceful local lease release; CAM Research status/pause controls, responsive UI. Production timer/pairing verification tracked separately; `docs/operations/RRUGC_AUTO_DISCOVER_DEPLOYMENT.md`.
- [x] RRUGC-SHOP-AWARE-FREE-DISCOVERY-CODE: read authenticated CAM Shop Library with existing Research Scout identity, sanitize US Etsy/Amazon names into public Tavily Keyless-only search queries, rotate queries within the unchanged 2/hour and 20/day budget, journal SQLite attempts and retain unverified Research Inbox flow. Flag defaults OFF; deployment and production canary are separate gates. `docs/operations/CAM_SHOP_AWARE_AUTO_DISCOVER.md`.
- [ ] RRUGC-ALWAYS-ON-DISCOVERY-V2: dedicated agent credential auto-provisioning via vetted secret channel, verified automatic Stage 0 quote+volume enrichment, cross-platform keyword/evidence association and provider fallback under approved terms.
- [ ] CAM-RESEARCH-SYNC-OPERATIONS: pair dedicated Research Scout Agent with operator-managed secret, verify authenticated production Research Inbox, then enable a non-root reviewed collection/sync schedule. Do not infer authenticated PASS from public-only smoke.

## Branding DNA & Product Creative Studio (2026-10-16)

- [x] BD-PC-SPEC-V1: consolidated approved system design and gated implementation plan (`docs/features/branding-dna/PRODUCT_CREATIVE_SYSTEM_SPEC.md` and `PRODUCT_CREATIVE_IMPLEMENTATION_PLAN.md`). **Documentation only, not an operational release.**
- [ ] BD-01..03: permitted product/gallery evidence → Product/Image Pick & source-verified Best Seller → versioned approved Brand DNA Core and Creative Playbook; never auto-promote inference or equate Pick with sales.
- [ ] PC-00..03: 3 Team Inputs → validated Product Master → editable 10-shot plan → SMS multi-angle mockup for Studio/Detail/Info/Variations **only**, approved Reference Scout for Lifestyle/UGC **only**, separate jobs/QA/final gallery.
- [ ] PC-04..06: Output Pick/Reject with neutral Unreviewed state; controlled supervised rule proposals, offline evaluation, human approval/canary/rollback; tenant permission & multi-device QA gates. See implementation plan for complete work packages.
- [x] SCOUT-MANAGER-EXPLICIT-STARTUP: opt-in `--start-automation` for reviewed Windows restarts, normal three-mode ownership and all-or-paused failure handling; default Idle and marketplace approval gates preserved. Native Windows CI and device installation remain separate release gates; see `apps/scout-manager-desktop/README.md`.
- [x] BD-BROWSER-PLATFORM-REVIEW: public CAM shop pilots need no shop-owner authorization or per-shop approval.json; a shared reviewed marketplace policy (or old exact-shop fallback) governs manually started, bounded browser observations. Etsy written grant remains exact-shop scoped; Amazon Agent Policy and 403/429 challenges remain enforced. Production dispatcher still disabled. Worker 0.2.13 / Manager release 0.1.45; native Windows release and authorized live pilot are separate gates.
