# Current State — Step 00 Codebase Audit

## Scope and evidence

This document records the repository state audited on 2026-07-18. Step 00 was read-only with respect to application code.

`docs/architecture/AGENT.md` is not present in the working tree or the available remote-tracking branches. The target-state comparison below therefore uses the Creative Asset Manager Agent Instructions, implementation roadmap, and ADR-001 through ADR-006 supplied with this task. `ROADMAP.md` and `REVIEW.md` are also absent; they were not created because this task explicitly limited outputs to this file and `IMPLEMENTATION_PLAN.md`.

The working tree already contained uncommitted application changes before this audit, notably the SQLAlchemy tag/rating implementation. Those files are included as current-state evidence but were not modified by Step 00.

## Executive summary

The repository is an early monorepo-shaped MVP. The active product is a React/Vite client and a FastAPI API that browse Google Drive or SharePoint through provider-specific HTTP clients. OAuth sessions, metadata indexing jobs, and the metadata fallback index are process-local. Directus is the optional persistent recursive-search index. A new app-owned SQLAlchemy database stores tags and ratings, defaulting to SQLite and creating tables during API startup.

The target architecture is not yet present: there is no durable tenant model, source registry, canonical asset registry, content SHA-256 identity, managed storage, JSONB AI metadata document, versioned search projection, Elasticsearch client/index, durable queue, worker, outbox, scheduler, or migration tool. PostgreSQL is supported only as an optional SQLAlchemy URL, not enforced as the source of truth.

## 1. Frameworks and repository layout

| Area | Current state | Exact evidence |
| --- | --- | --- |
| API | Python FastAPI 0.116.1, Uvicorn 0.35, Pydantic v2 transitively, HTTPX 0.28.1 | `apps/api/requirements.txt`, `apps/api/app/main.py` |
| Web client | React 18.3.1, React DOM, TypeScript 5.7.3, Vite 5.4.14 | `apps/client/package.json`, `apps/client/vite.config.ts`, `apps/client/tsconfig.json` |
| Persistence | SQLAlchemy 2.0.41; psycopg 3.2.9 available; SQLite default | `apps/api/requirements.txt`, `apps/api/app/core/database.py` |
| Workspace | pnpm workspace declaration for `apps/client` and `packages/*`, but the active client uses npm and has the only lockfile | `pnpm-workspace.yaml`, `apps/client/package-lock.json`, `Makefile` |
| Apps | `apps/api`, `apps/client`, and an empty `apps/worker` scaffold | `apps/api`, `apps/client`, `apps/worker` |
| Shared packages | `sdk`, `types`, `ui`, and `utils` are placeholder packages with README files only | `packages/*` |
| Infrastructure | Docker, compose, Nginx, PostgreSQL, Redis, Directus, and Filestash paths are placeholders; active compose/Docker/Nginx files are zero bytes | `infrastructure/`, `apps/api/Dockerfile`, `apps/worker/Dockerfile` |

The package-manager convention is inconsistent: the root declares pnpm workspaces, while `make client`, CI, and the committed lockfile use npm. The Python side uses a plain pinned `requirements.txt` and a local virtual environment created by `scripts/dev-api.sh`.

## 2. Database, ORM, and migration conventions

`apps/api/app/core/database.py` creates a synchronous SQLAlchemy engine and `SessionLocal`. Development defaults to `apps/api/data/creative_asset_manager.db` and upgrades it to the single Alembic head at startup. Production requires a non-SQLite `DATABASE_URL`, validates connectivity and the current Alembic head, and never changes schema during API startup. Built-in `public` and `draft` tags are seeded only through the explicit idempotent `python -m app.operations.tag_cli seed-system-tags` command. SQLAlchemy pool size, overflow, timeout, recycle, and PostgreSQL connect timeout are centrally configurable; the engine is disposed during application shutdown.

`apps/api/alembic.ini` points to the versioned migration chain under `database/migrations`; revisions 0001 through 0018 own the application schema and include downgrade behavior or explicit data-preservation notes. Production releases run Alembic separately before API startup. `app.core.config.Settings` centralizes database URL and pool validation, while the migration environment accepts either the deployment URL or an explicitly supplied validated connection.

Current app-owned tables:

| Table | Identity and constraints | Purpose |
| --- | --- | --- |
| `tags` | String `id` PK; globally unique `name` | System/custom tag definitions; currently `public` and `draft` share `group_key=visibility` |
| `asset_metadata` | Integer PK; unique `(account_id, provider, item_id)`; rating check `1..5` | Per-cloud-item tags and rating only |
| `asset_tag_assignments` | Composite PK `(asset_metadata_id, tag_id)` with cascading FKs | Many-to-many tag assignments |

Evidence: `apps/api/app/modules/tag/model.py`, `apps/api/app/modules/tag/repository.py`, `apps/api/app/modules/metadata/model.py`, `apps/api/app/modules/metadata/repository.py`.

This table named `asset_metadata` is distinct from the optional Directus collection also named `asset_metadata`. The SQLAlchemy table stores tags/rating; the Directus collection stores browse/search projection fields. The shared name is a material migration and operational risk.

## 3. Tenant, account, asset, and identity model

There is no `tenant`, organization, workspace, user, membership, external source, or source credential table. `account_id` is derived at request time from the selected provider OAuth profile (`user.id`, then email), with a `{provider}:developer` fallback. Microsoft's configured Entra tenant ID is an OAuth authority selector, not an application tenant boundary.

Evidence: `apps/api/app/core/cloud_account.py`, `_account_id` in `apps/api/app/modules/explorer/router.py`, and provider session modules.

There is no canonical asset table. The explorer DTO `AssetNode` uses the provider item ID as `id`. Google IDs are native Drive IDs. SharePoint IDs are application-generated opaque strings encoding node kind plus Graph site/drive/item IDs. Directus metadata generates a SHA-256 row key from `provider:account_id:item_id`; this is a source-record key, not a content hash. No code hashes downloaded asset bytes.

Evidence: `apps/api/app/modules/explorer/schema.py`, `apps/api/app/providers/google/mapper.py`, `apps/api/app/providers/microsoft/mapper.py`, `MetadataService.stable_id()` in `apps/api/app/modules/metadata/service.py`.

Gap against target identity:

- Missing `tenant_id` on all authoritative data.
- Missing external source registry and stable `external_source_id`.
- Current source identity approximates `(account_id, provider, item_id)` rather than `(tenant_id, external_source_id, external_asset_id)`.
- Missing tenant-scoped SHA-256 content identity and uniqueness constraint.
- Renames/moves are safe for provider item identity, but reconnecting sources, tenant separation, content deduplication, and cross-source copies are not modeled.

## 4. Source integrations

### Google Drive

OAuth uses Authorization Code with PKCE through `google-auth-oauthlib`, requests OpenID/profile/email and Drive read-only scope, validates the required scope, obtains user info, and stores access/refresh tokens in an in-process dictionary keyed by an HTTP-only cookie. Refresh is performed in the API process. A static `GOOGLE_DRIVE_ACCESS_TOKEN` is a developer fallback.

`GoogleDriveClient` calls Drive v3 directly through HTTPX. It supports item lookup, paginated child listing (including shared drives), folder-only listing, and streamed `alt=media` download. Transient 429/5xx list/get failures receive at most three attempts with simple exponential delay or integer `Retry-After`.

Exact files:

- `apps/api/app/modules/auth/router.py`
- `apps/api/app/providers/google/auth.py`
- `apps/api/app/providers/google/drive.py`
- `apps/api/app/providers/google/mapper.py`
- `apps/api/app/providers/google/changes.py` (empty incremental-sync placeholder)
- `apps/api/app/providers/google/permissions.py` (empty placeholder)

### SharePoint

Microsoft OAuth is implemented independently with hand-built Authorization Code + PKCE requests. It requests OpenID/profile/email/offline access, `User.Read`, `Sites.Read.All`, and `Files.Read.All`; tokens are also held only in process memory. A static `SHAREPOINT_ACCESS_TOKEN` is a developer fallback.

`SharePointClient` uses Microsoft Graph to discover accessible sites, enumerate document libraries, list DriveItems, obtain Graph thumbnails, and stream `/content`. It can be restricted to one configured site. Its retry behavior matches the Google client. The normalized tree is SharePoint root → site → document library → folder/file.

Exact files:

- `apps/api/app/modules/auth/microsoft_router.py`
- `apps/api/app/providers/microsoft/auth.py`
- `apps/api/app/providers/microsoft/sharepoint.py`
- `apps/api/app/providers/microsoft/mapper.py`
- `apps/api/app/providers/sharepoint/{graph,delta,mapper,permissions}.py` (empty duplicate/legacy scaffold)

Both provider clients return the shared `AssetNode`, which is useful normalization, but `ExplorerService` selects concrete provider classes directly. Provider SDK/HTTP details do not leak into controllers, yet business services still depend on concrete adapters rather than provider contracts.

## 5. Current data flows

### Interactive browse

1. React initializes both `/api/auth/google/session` and `/api/auth/microsoft/session` from `apps/client/app/hooks/useDriveExplorer.ts`.
2. The user selects a source; the client calls `/api/explorer/children` or `/api/explorer/folders`.
3. `apps/api/app/modules/explorer/router.py` resolves an in-memory OAuth session/token and derives `account_id`.
4. `ExplorerService` selects `GoogleDriveClient` or `SharePointClient`, loads parent and children, and maps them to `AssetNode`.
5. The listing is returned immediately; an untracked `asyncio.create_task` asynchronously mirrors the listing to Directus or process memory.
6. The client caches tree/listing data locally and prefetches folders on pointer hover.

### Recursive metadata indexing and search

1. After provider authentication, the client posts `/api/explorer/index/start` and polls `/api/explorer/index/status`.
2. `start_index_job` stores status in process dictionaries and creates an `asyncio` task.
3. `ExplorerService.search_subtree` loads the existing Directus/process-memory subtree, breadth-first crawls missing/stale folders with configurable concurrency, and upserts rows.
4. Search normalizes names, applies token substring/fuzzy matching in Python, sorts scores, and returns `AssetNode` results.
5. Interactive search may use `/api/explorer/search/stream`, which streams NDJSON progress/results while repeating or extending indexing.

Directus is therefore being used as a semi-authoritative search cache, while process memory is the fallback. There is no Elasticsearch flow, mapping, alias, bulk indexer, or rebuild operation.

### Thumbnail and media preview

1. Google list responses carry `thumbnailLink`; SharePoint child requests expand Graph `thumbnails` and select the largest available URL.
2. `AssetGrid.tsx` places those provider URLs directly in `<img>` elements and falls back to an icon on error.
3. Double-click preview opens `MediaViewer.tsx`, whose image/video source is `/api/explorer/media/{item_id}?provider=...`.
4. The API forwards `Range`, streams Drive `alt=media` or Graph `/content` without buffering, passes through range/cache validators, and sets private five-minute caching.

There is no managed original download, checksum calculation, stored rendition, durable thumbnail job, signed managed-storage URL, malware validation, or download SSRF boundary.

### Tags and ratings

1. The client queries `/api/tags` and `/api/metadata/query` for visible item IDs.
2. Bulk tag assignment and one-to-five-star rating call `/api/tags/assign` and `/api/metadata/rating`.
3. Thin routers invoke domain-like services, which invoke SQLAlchemy repositories.
4. Rows are keyed by current `(account_id, provider, item_id)` and committed synchronously.

## 6. Queues, workers, scheduling, retries, and idempotency

No durable queue technology is installed or configured. `apps/worker/main.py`, its job modules, requirements, Dockerfile, and README are empty. Redis infrastructure is a placeholder. There is no scheduler, cron manifest, outbox, dead-letter queue, lease, heartbeat, or persisted retry counter.

Current background work consists of `asyncio.create_task` in:

- `apps/api/app/modules/explorer/indexing.py` for per-process account/provider indexing jobs.
- `apps/api/app/modules/metadata/service.py` for best-effort listing upserts.
- `apps/api/app/modules/explorer/router.py` for the lifetime of a streaming search request.

Tasks and statuses disappear on restart, cannot coordinate across API replicas, and have no durable idempotency key. Duplicate index starts are suppressed only while the same in-process task remains alive. Provider GET calls have local three-attempt retry; OAuth exchange, Directus writes, database transactions, and media streams do not use a shared retry policy.

## 7. Module conventions

The intended backend convention is feature modules with `model.py`, `schema.py`, `repository.py`, `service.py`, and `router.py`, plus provider adapters under `app/providers`. Tags and app-owned ratings mostly follow router → service → repository. Routers use FastAPI dependency injection for SQLAlchemy sessions.

The convention is incomplete:

- Explorer `model.py` and `repository.py` are empty; its service combines provider selection, crawl orchestration, indexing policy, search scoring, and demo behavior.
- Explorer router contains account/token/provider error selection and stream orchestration.
- Metadata has two unrelated service paths under one module: Directus search indexing in `service.py` and SQLAlchemy tags/ratings in `asset_service.py`.
- Collection, dashboard, listing, permission, search, storage, and user modules are entirely empty scaffolds.
- `app/core/dependencies.py`, `logger.py`, and `security.py` are empty.

The frontend uses small presentational components, but `apps/client/app/hooks/useDriveExplorer.ts` is a large orchestration hook containing auth, navigation, caches, prefetch, indexing polling, search streaming, tags, rating, and filtering. Shared package scaffolds are unused.

## 8. Authentication and authorization

Authentication means possession of a provider-specific opaque session cookie whose server-side record exists in that API process. Cookies are HTTP-only, `SameSite=Lax`, and optionally Secure. OAuth state/PKCE transactions are also process-local with a ten-minute TTL.

There is no first-party app session, user table, tenant membership, role, permission policy, CSRF token, session persistence, token encryption at rest, audit trail, or authorization check over assets/tags. Any caller with a valid browser session can mutate metadata for the derived cloud account. `/api/tags` is global and unauthenticated. Developer access tokens and mock Google data can be used when no OAuth session exists.

## 9. Tests and commands

There are no committed Python or TypeScript unit/integration/e2e test files and no pytest/Vitest/Playwright dependency or configuration.

Current validation commands:

- Client: `npm run build` (`tsc -b && vite build`) and `npm run typecheck` from `apps/client/package.json`.
- API CI: `python -m compileall -q apps/api/app`, import FastAPI app, and two inline Python smoke tests for Google PKCE and Microsoft PKCE/opaque IDs.
- Script syntax: `bash -n scripts/dev-api.sh`.
- CI workflow: `.github/workflows/ci.yml`, using Node 22 and Python 3.12.

`npm install` is used in CI rather than `npm ci`, and the cache dependency path points to `package.json` rather than the lockfile. There are no database/provider contract tests, migration tests, authorization tests, worker tests, or search semantics tests.

## 10. Logging, metrics, and tracing

Several modules use standard-library `logging.getLogger(__name__)`; OAuth callbacks add a short request ID to relevant messages, and indexing logs failures/skipped folders. No central log configuration exists because `apps/api/app/core/logger.py` is empty. Uvicorn supplies default access/application logging.

Structured logging and metrics remain limited, but HTTP operations now expose separate `/live`, `/ready`, and `/version` endpoints. Readiness checks PostgreSQL and conditionally Elasticsearch without returning connection details or exceptions. Build identifiers and proxy-header trust are validated centrally; forwarded headers are honored only from configured IP/CIDR networks. `/health` remains as the legacy liveness-compatible endpoint.

## 11. Configuration and feature flags

Configuration is environment-variable driven via `load_dotenv()` and direct `os.getenv` calls. `.env.example` documents OAuth, Directus indexing limits/TTL/concurrency, optional static provider tokens, SharePoint site restriction, and `DATABASE_URL`. The real `apps/api/.env` exists locally and is ignored; it was not inspected.

There is no typed, validated settings object and no general feature-flag mechanism. Current conditional behavior is implicit:

- Directus configured versus process-memory fallback.
- OAuth session versus static token/mock Google data.
- Optional SharePoint single-site discovery.
- Secure-cookie booleans.
- Database URL unset versus SQLite fallback.

These are runtime modes, not rollout-safe flags with ownership, defaults, or removal criteria.

## 12. Target architecture gap map

| Accepted decision / target | Current implementation | Gap severity |
| --- | --- | --- |
| ADR-001 PostgreSQL source of truth | Optional PostgreSQL driver/URL; SQLite default; Directus/memory is recursive-search persistence | Critical |
| ADR-002 tenant-scoped SHA-256 content identity | SHA-256 only hashes source-key text for a Directus row ID | Critical |
| ADR-003 dynamic AI metadata JSONB | No AI metadata document or JSONB column | Critical |
| ADR-004 stable versioned search projection | Ad hoc Directus fields plus Python name matching | Critical |
| ADR-005 independent providers | Separate concrete adapters normalized to `AssetNode`, but service imports them directly; no source/storage/AI contracts | Partial foundation |
| ADR-006 asynchronous processing in workers | Process-local API tasks; worker application is empty | Critical |
| Elasticsearch rebuildable index | No dependency, client, mapping, alias, document builder, or reindex flow | Critical |
| Idempotent external processing | Only in-process duplicate-task suppression and DB uniqueness for tag metadata | Critical |
| Incremental synchronization | Google changes and SharePoint delta files are empty | Critical |
| Managed asset storage | Storage module and worker job are empty | Critical |

## 13. Exact relevant files

### Active backend

- `apps/api/app/main.py`
- `apps/api/app/core/database.py`
- `apps/api/app/core/cloud_account.py`
- `apps/api/app/modules/auth/router.py`
- `apps/api/app/modules/auth/microsoft_router.py`
- `apps/api/app/modules/explorer/{router,schema,service,indexing}.py`
- `apps/api/app/modules/metadata/{model,repository,router,schema,service,asset_service}.py`
- `apps/api/app/modules/tag/{model,repository,router,schema,service}.py`
- `apps/api/app/providers/google/{auth,drive,mapper}.py`
- `apps/api/app/providers/microsoft/{auth,sharepoint,mapper}.py`

### Active frontend

- `apps/client/app/App.tsx`
- `apps/client/app/hooks/useDriveExplorer.ts`
- `apps/client/app/hooks/useResizableSidebar.ts`
- `apps/client/app/components/{AssetGrid,DriveTree,DriveEmpty,EmptyAssets,MediaViewer,Sidebar}.tsx`
- `apps/client/app/types.ts`
- `apps/client/app/utils/searchAssets.ts`
- `apps/client/styles/global.css`

### Build, operations, and configuration

- `README.md`
- `.env.example`
- `Makefile`
- `pnpm-workspace.yaml`
- `apps/client/package.json`
- `apps/client/package-lock.json`
- `apps/api/requirements.txt`
- `scripts/dev-api.sh`
- `.github/workflows/ci.yml`

### Important empty scaffolds

- `apps/worker/` and `apps/worker/jobs/`
- `apps/api/app/modules/{collection,dashboard,listing,permission,search,storage,user}/`
- `apps/api/app/providers/google/{changes,permissions}.py`
- `apps/api/app/providers/sharepoint/{graph,delta,mapper,permissions}.py`
- `database/migrations/`
- `infrastructure/{docker,nginx,postgres,redis}/`

## 14. Migration risks and blockers

1. **Missing architecture source file:** `docs/architecture/AGENT.md` must be restored or its supplied instructions formally committed before later steps can prove compliance.
2. **Dirty working tree:** current SQLAlchemy metadata work is uncommitted. Future architecture PRs need a clean baseline or an explicit decision to adopt/rework it.
3. **Two `asset_metadata` stores:** Directus search rows and SQLAlchemy tag/rating rows have different schemas and authority. Data ownership and rename strategy must be decided before migration.
4. **No tenant key:** existing account-derived records cannot be deterministically assigned to application tenants without a migration mapping.
5. **No external source ID:** `provider + account_id` is not enough to distinguish multiple connections of the same provider/account or reconnections.
6. **No content bytes/hash:** deduplication requires downloading content, which affects provider quotas, egress, large-file streaming, security, and backfill duration.
7. **Migration discipline:** schema ownership is now Alembic-only. Migration `0018_legacy_metadata_schema` safely adopts the legacy metadata/tag tables without deleting their data on downgrade; production startup fails until the deployed schema reaches the single head.
8. **Ephemeral OAuth and jobs:** restarts invalidate sessions and processing state; horizontal scaling would create inconsistent account and job views.
9. **Search cutover:** current UI depends on index-on-login progress and Directus fallback. Elasticsearch v2 needs dual-write/read flags and a reproducible backfill before cutover.
10. **SharePoint identity:** current opaque node IDs include hierarchy node kinds; source records must retain raw site/drive/item IDs separately so identity is stable and queryable.
11. **Global tags:** tags are not tenant-scoped. Adding tenant uniqueness requires resolving current global rows and assignments.
12. **No automated behavioral safety net:** provider, persistence, auth, and query behavior need tests before structural migration.

## 15. Questions requiring an architecture decision

- What is the initial tenant boundary: one tenant per deployment, per customer organization, or explicit multi-tenant memberships?
- Should existing Directus metadata be imported into PostgreSQL, temporarily dual-read, or discarded and rebuilt from providers?
- Is Directus retained as an admin UI after PostgreSQL becomes authoritative, or removed from runtime data flow?
- Which durable queue/runtime is preferred for `apps/worker` (for example Redis-backed Dramatiq/Celery, PostgreSQL-backed jobs, or another platform)?
- Which managed storage implementation is first (S3-compatible, Azure Blob, local development adapter), and what retention policy applies?
- What Elasticsearch/OpenSearch deployment and version must mappings target?
- How should legacy `account_id` rows map to tenants and external source connections?
- Should source OAuth credentials remain user-owned connections or become tenant-managed service connections?
