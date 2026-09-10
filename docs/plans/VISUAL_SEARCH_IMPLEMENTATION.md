# Visual Search  Repository-specific ADR and implementation plan

**Status:** Historical VS-00 architecture audit and ADR. The Visual Search architecture has since been implemented through VS-11; VS-12 release hardening is active. VS-12A — end-to-end canary tenant eligibility — is **COMPLETE**. VS-12B — pagination and committed visual query state — is **NEXT** after VS-12A main integration. Broad production enablement remains blocked and this document does not authorize production changes.
**Audit date:** 2026-09-07. **Audited main:** `65603905a9be766de32b4f39f33199822281ae77`.
**Master guide:** [CAM_VISUAL_SEARCH_PINTEREST_IMPLEMENTATION_GUIDE.md](CAM_VISUAL_SEARCH_PINTEREST_IMPLEMENTATION_GUIDE.md), whose `f393dfc` baseline is superseded by current source.

## Current hardening context

This ADR records the original VS-00 decisions. Current source implements the
later phases; retain the historical decisions below as context rather than as a
claim that Visual Search is production-ready. New Visual Search queries,
embedding/index work, and explicit backfill require both
`VISUAL_SEARCH_ENABLED=true` and tenant membership in
`VISUAL_SEARCH_CANARY_TENANT_IDS`. An empty allowlist denies new Visual Search
work. Retire/delete reconciliation remains permitted after de-allowlisting only
to remove already-derived visual state; it must not generate a new embedding.

VS-12A is complete. VS-12B is the next task. Broad production rollout remains
blocked pending the remaining correctness, relevance, VPS, and rollback gates.

| Area | Current implementation |
|---|---|
| FastAPI | `apps/api/app/main.py:create_app`, including `search_router`. |
| Search V3 | `apps/api/app/modules/search/router.py`: `GET /api/v1/search/capabilities`, `GET /suggestions`, `POST /api/v1/search`; `query_parser.py`, `query_builder.py`, `schema.py`, `runtime.py`. |
| Authorization | `SEARCH_READ=require_permission("search.read")`; tenant is only `CurrentPrincipal.active_tenant_id`. |
| Candidate restriction | `router.py:_search_scope_filters` always adds a tenant term and additionally source/viewer folder clauses before the ES request. `_viewer_scope_filter` uses `ViewerFolderScopeService`; no scope is `match_none`. |
| Hydration | `router.py:_hydrate_search_hits` joins `AssetSourceLinkModel -> SourceAssetModel -> ExternalSourceModel`, rechecks tenant and ignores deleted sources. |
| Cursor/filter | `query_builder.py:ElasticsearchQueryBuilder`, `encode_search_cursor`, `decode_search_cursor`, `search_request_fingerprint`; runtime uses PIT/`search_after`. |
| ES abstraction | `infrastructure/search/elasticsearch_v2.py:ElasticsearchV3Index` over `httpx==0.28.1`, not the official Python ES client. |
| ES lifecycle | `search/index_lifecycle.py`, `index_adoption.py`, `governance_router.py`, `operations/search_index_cli.py`, `operations/search_cli.py`. |
| Assets/pipeline | `assets/model.py`; `pipeline/model.py, repository.py, service.py, state.py, handlers.py`. |
| Source bytes | `pipeline/content_resolver.py:SourceAssetContentResolver.open`, then `SourceAssetPipelineContentResolver` and `ProviderDownloadStage`. |
| Production worker | `apps/worker/main.py -> processing/bootstrap.py:build_worker_runtime -> processing/runtime.py:WorkerRuntime`. Durable jobs: `processing/model.py, repository.py`; policy claim: `processing_policy/claim.py`. |
| Frontend | `apps/client/app/App.tsx`, `hooks/useSearchV3.ts`, `components/SearchControls.tsx`, `components/AssetGrid.tsx`, `components/AssetDetailsPanel.tsx`. |
| Deploy | `scripts/cam-rebuild-backend.sh`, `deploy/systemd/*worker*.service`, `infrastructure/docker/docker-compose.prod.yml`. |

## Current architecture

```text
Browser -> /api/v1/search -> Search V3 router
  -> tenant/source/viewer filters -> ES v3 read alias
  -> live tenant-safe DB hydration -> current AssetGrid

Source sync -> durable processing job -> image worker
  -> download/store/analyze -> projection -> asset_index -> ES Search V3
PostgreSQL remains authoritative for assets, sources, ownership and permissions.
```

Search V3s physical indices are `{prefix}-v3-{version}`, aliases are `{prefix}-v3-read/write`; mapping is strict. V3 mapping includes tenant/source/ancestor IDs, text, media and metadata fields but no vector. Development, CI and production compose pin **Elasticsearch 8.15.3** (`infrastructure/docker/docker-compose.*.yml`, `.github/workflows/ci.yml`). Production config documents loopback ES, 2 CPU and 2 GiB memory limits (`deploy/production.env.example`). This audit intentionally made no production query; VS-03 must use an ES 8.15.3 integration test to prove the exact KNN syntax/mapping.

## Asset lifecycle and safe bytes

```text
source sync -> SourceAssetModel/link -> source_asset_download
 -> tenant-scoped stream/content hash -> AssetPipelineModel
 -> asset_store -> asset_analyze -> search_projection_build
 -> asset_index/search_index_sync -> Search V3 document
```

Future visual work: content creation/replacement queues a versioned visual job; safe decode/fingerprint/encode/upsert produces derived state. Metadata-only changes must not re-encode; delete/archive removes the visual document. SHA-256 is exact content identity; pHash is optional future near-duplicate data and not semantic retrieval.

Reuse `SourceAssetContentResolver.open`: it joins tenant-scoped source data, resolves the tenant OAuth context, opens provider streams and accepts no arbitrary URL. Prefer a bounded managed derivative when available, otherwise this bounded source stream. Preview URLs/thumbnails are presentation paths, not an unreviewed encoder input API.

## Decisions

| Decision | Options | Selected | Reason / rollback |
|---|---|---|---|
| Vector index | extend strict Search V3 / separate index | **Separate versioned visual index** `{prefix}-visual-{schema}-read/write` | Keeps strict, live Search V3 and its alias lifecycle untouched; permits model/mapping rollback. Disable visual flags or move visual alias back; never touch Search V3 aliases. |
| Tenant safety | filter after KNN / filter during KNN | **During KNN** | Include tenant plus source/viewer-folder filters in the ES candidate request. DB hydration is a second safety check, never primary isolation. |
| Queue | new queue / existing durable processing | **Existing queue** | Reuse lease, retry, idempotency, worker health and tenant/provider concurrency. |
| Encoder location | FastAPI / worker interpreter / isolated service | **Isolated local encoder process/venv, client used by image worker** | Avoid model RSS/dependency failure affecting API/general worker. No service is created in VS-00. |
| Model | hardcode now / benchmark first | **SigLIP baseline, pinned revision** | User decision on 2026-09-08 after isolated CPU technical benchmark; relevance benchmark is explicitly deferred. |

### Encoder candidates

| Candidate | License/dependencies | Shared embedding | Operational assessment |
|---|---|---|---|
| `google/siglip-base-patch16-224` | Apache-2.0; Transformers/PyTorch or later ONNX | image/text; verify projection dimension from pinned revision | 224px and commercially friendly. The Hub safetensor is about 813 MB, so shared process deployment is unsafe without measurement. |
| `openai/clip-vit-base-patch32` | Verify fixed revision/checkpoint license before approval; Transformers/OpenCLIP/ONNX | image/text; commonly 512-D, verify in VS-02 | Reasonable CPU baseline and ONNX path; relevance and legal metadata must be tested. |

The SigLIP model card documents Apache-2.0 licensing, 224px model use and image-text retrieval. [Source](https://huggingface.co/google/siglip-base-patch16-224)

### Baseline selection update — 2026-09-08

Per explicit user direction, V1 uses google/siglip-base-patch16-224 at
revision 7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed as the baseline encoder:
768 dimensions, cosine-normalized vectors, and preprocess version
siglip-224-transformers-4.46.3-v1. The relevance/image-fixture benchmark is
deferred by that direction; it remains required before broad production
rollout. The model is still loaded only by the isolated encoder environment,
never by FastAPI or the shared worker.

**VS-02 benchmark gate:** development-only pinned model, fixed CAM corpus; cold load, RSS after load, single/20-sequential image and crop p50/p95, dimension/normalization, and relevance labels. No model is installed in production before this gate.

## Embedding, API, crop and ranking contract

Derived record identity:
```text
tenant_id + asset_id + content_sha256 + embedding_schema_version
encoder_name, encoder_revision, dimension, preprocess_version,
similarity=cosine, generated_at, visual_embedding
```
Start `visual_embedding_v1`; any model/dimension/preprocess/distance change creates v2 index/alias and retains v1 through a rollback window.

Future routes follow current namespace:
* `POST /api/v1/search/visual/by-asset`
* `POST /api/v1/search/visual/upload`

By-asset authorizes the asset before vector/byte access. Upload is authenticated, bounded by bytes and decoded pixels, safely decoded, has no caller file path/URL and is deleted on all outcomes. Responses reuse asset summaries and opaque cursor semantics; never expose vectors, signed URLs or fake similarity percentages.

Crop coordinates are normalized after EXIF orientation:
```json
{"x":0.21,"y":0.18,"width":0.52,"height":0.61}
```
Validate bounds/minimum pixels, crop server-side, RGB-convert and encode. Do not encode on pointer move.

Stage 1 is ES ANN/KNN with tenant/viewer filters in retrieval (candidate count benchmarked, initially 200500). Stage 2 belongs in a separately testable `modules/visual_search/ranking.py`: suppress duplicates, optional text/metadata rerank, diversity, then paginate.

## Worker, frontend, flags and failure isolation

Image worker is the production owner of non-video jobs; `worker_roles.py` and `bootstrap.py` must remain the only job/scheduler framework. Visual indexing runs as a new optional image-role job whose handler calls the isolated encoder client. API/Search V3/core browsing health cannot depend on encoder availability.

VS-01 should add default-off Settings/env flags:
`VISUAL_SEARCH_ENABLED`, `VISUAL_SEARCH_UPLOAD_ENABLED`, `VISUAL_SEARCH_CROP_ENABLED`, `VISUAL_SEARCH_HYBRID_TEXT_ENABLED`, `VISUAL_SEARCH_BACKFILL_ENABLED`.
Use existing tenant policy/config conventions for canary eligibility; do not accept tenant from client input.

VS-08 uses the existing global search surface, `AssetGrid` and details actions: camera/visual action, upload/choose-existing, reference preview/crop, Find Similar and browser history. Do not duplicate the asset browser.

## Resource, observability and backfill

The VPS profile in the master guide is 3 vCPU/~5.8 GiB/no swap; ES is documented at 2 GiB. Adding PyTorch to the API/shared worker venv is therefore not safe. Start isolated encoder and backfill at concurrency **1**, reject/defer when capacity is unavailable, and move encoder remotely if measurement requires it.

Extend the current redacted JSON worker logs with queued/success/failure/skipped, schema/model, encode/KNN/rerank/total latency, result/empty count, capacity rejection, checkpoint and encoder RSS/CPU. Never log query bytes, vectors, signed URLs, OAuth values or other-tenant data.

VS-10 is explicit tooling only: dry-run default, tenant scope/allowlist, deterministic asset-id checkpoint, schema required, small batch/max, one slot, throttle, retry/backoff, stop/resume and redacted progress. Do not run visual backfill alongside Dola/Chromium or broad ES reindex without evidence.

## Required tests and rollout

Current tests to extend: `tests/modules/search/test_api.py`, `test_index_lifecycle_r3.py`, `test_coverage_audit.py`, `tests/infrastructure/search/test_elasticsearch_v2.py`, `tests/integration/test_elasticsearch.py`, `tests/integration/test_pipeline_e2e.py`, `tests/modules/processing_policy/test_policy_and_claim.py`, `useSearchV3.*.test.ts`, `AssetGrid.test.ts`.

Release blockers: tenant A cannot query/crop B asset; KNN returns no B candidate; filters/cursor cannot cross tenant; malformed/oversized/decompression inputs; ES 8.15.3 mapping/KNN integration; Search V3 regression; all visual flags disabled. Build a 100300 query human-labelled benchmark (P@10, Recall@10, NDCG@10, empty rate, p50/p95).

Rollout: all flags off -> internal tenant -> by-asset -> observe -> upload -> crop -> hybrid -> gradual expansion. Rollback: disable flags, reverse visual alias; do not delete old visual index during the window.

## Proposed files

### VS-01
* `apps/api/app/core/config.py`
* `apps/api/app/modules/visual_search/{__init__.py,contracts.py,schema.py,service.py}`
* `apps/api/tests/modules/visual_search/{test_contracts.py,test_feature_flags.py}`
* `.env.example`, `deploy/production.env.example`

### VS-02
* `apps/api/app/modules/visual_search/{preprocess.py,encoder.py,fingerprint.py,encoder_client.py}`
* `apps/api/app/operations/visual_search_benchmark.py`
* `apps/api/tests/modules/visual_search/{test_preprocess.py,test_encoder_contract.py,test_fingerprint.py}`
* `docs/operations/VISUAL_SEARCH_BENCHMARK.md`

### VS-03
* `apps/api/app/modules/visual_search/{elasticsearch.py,repository.py,ranking.py,service.py}`
* `apps/api/tests/modules/visual_search/{test_elasticsearch.py,test_tenant_isolation.py}`
* `apps/api/tests/integration/test_visual_search_elasticsearch.py`

## VS-01 acceptance criteria and known unknowns

**VS-01 readiness: READY.** It is contracts/config only: flags off, no ML import/load, no ES mapping, migration, frontend behavior or deployment; current Search V3 remains unchanged.

Later-phase blockers: production cluster version/KNN capability must be proved in ES 8.15.3 integration; model revision/license/dimension/quality/RSS are benchmark outcomes; current scope-filter helpers live in the Search router and should be extracted to a shared tested dependency before VS-03 rather than imported from route code.
