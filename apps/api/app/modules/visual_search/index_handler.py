from __future__ import annotations

import asyncio
from collections.abc import Mapping

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3RequestError
from app.modules.assets.content_resolver import SourceAssetContentTransient, SourceAssetContentUnavailable
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.pipeline.mime_types import is_supported_image_mime_type
from app.modules.search.source_index import SearchSourceIndexResolver
from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.elasticsearch import VisualIndexDocument, VisualSearchElasticsearchIndex
from app.modules.visual_search.fingerprint import sha256_fingerprint
from app.modules.visual_search.lifecycle import VISUAL_EMBEDDING_SCHEMA_VERSION, visual_index_job_enabled
from app.modules.visual_search.preprocess import VisualImagePreparationError, decode_visual_image
from app.modules.visual_search.metrics import VISUAL_SEARCH_METRICS


class VisualIndexSyncJobHandler:
    """Worker-side lifecycle owner for a single visual embedding projection.

    The encoder is an injected isolated client.  This handler must never import
    torch/transformers or instantiate the SigLIP model in CAM's shared worker.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        settings = self.settings or get_settings()
        if not visual_index_job_enabled(settings, context.job.tenant_id):
            return JobHandlerResult.non_retryable("visual_search_disabled", "Visual search is disabled.")
        if context.cancellation_requested.is_set() or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        provider = context.dependencies.resources.get("visual_index_provider")
        encoder_client = context.dependencies.resources.get("visual_encoder_client")
        resolver = context.dependencies.resources.get("visual_content_resolver")
        if not isinstance(provider, VisualSearchElasticsearchIndex):
            return JobHandlerResult.retryable("visual_index_unconfigured", "Visual index is unavailable.")
        try:
            if context.job.payload.get("operation") == "reconcile_retired_source":
                self._run(context, provider.delete_asset(tenant_id=context.job.tenant_id, asset_id=context.job.entity_id))
                return JobHandlerResult.completed()
            if encoder_client is None or resolver is None:
                return JobHandlerResult.retryable("visual_index_unconfigured", "Isolated encoder is unavailable.")
            document = self._document(context, provider)
            if document is None:
                self._run(context, provider.delete_asset(
                    tenant_id=context.job.tenant_id, asset_id=context.job.entity_id,
                ))
                return JobHandlerResult.completed()
            content = self._read_content(context, resolver, document.content_sha256)
            prepared = decode_visual_image(content)
            embedding = encoder_client.get_encoder().encode_image(prepared.image)
            if embedding.descriptor.embedding_schema_version != VISUAL_EMBEDDING_SCHEMA_VERSION:
                return JobHandlerResult.non_retryable("visual_index_schema_mismatch", "Isolated encoder schema does not match the queued visual index.")
            document = VisualIndexDocument(
                tenant_id=document.tenant_id, asset_id=document.asset_id,
                content_sha256=document.content_sha256, embedding=embedding,
                source_id=document.source_id, source_provider=document.source_provider,
                media_kind=document.media_kind, mime_type=document.mime_type,
                extension=document.extension, design_type=document.design_type,
                ancestor_ids=document.ancestor_ids,
            )
            self._run(context, provider.upsert(document))
            VISUAL_SEARCH_METRICS.observe_indexing("success")
            return JobHandlerResult.completed()
        except SourceAssetContentTransient as exc:
            VISUAL_SEARCH_METRICS.observe_indexing("error")
            return JobHandlerResult.retryable("visual_index_source_unavailable", str(exc))
        except SourceAssetContentUnavailable as exc:
            VISUAL_SEARCH_METRICS.observe_indexing("error")
            return JobHandlerResult.non_retryable("visual_index_source_unavailable", str(exc))
        except VisualImagePreparationError as exc:
            VISUAL_SEARCH_METRICS.observe_indexing("error")
            return JobHandlerResult.non_retryable(exc.code, str(exc))
        except ElasticsearchV3RequestError as exc:
            VISUAL_SEARCH_METRICS.observe_indexing("error")
            if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 429:
                return JobHandlerResult.non_retryable("visual_index_elasticsearch_rejected", str(exc))
            return JobHandlerResult.retryable("visual_index_elasticsearch_unavailable", str(exc))
        except Exception as exc:
            VISUAL_SEARCH_METRICS.observe_indexing("error")
            return JobHandlerResult.retryable("visual_index_failed", str(exc))

    @staticmethod
    def _run(context: JobHandlerContext, operation) -> None:
        executor = context.dependencies.resources.get("async_executor")
        if executor is None:
            asyncio.run(operation)
        else:
            executor.run(operation)

    @staticmethod
    def _read_content(context: JobHandlerContext, resolver, expected_sha256: str) -> bytes:
        async def read() -> bytes:
            chunks: list[bytes] = []
            size = 0
            async with resolver.open(
                tenant_id=context.job.tenant_id,
                source_asset_id=str(context.job.payload["source_asset_id"]),
            ) as stream:
                async for chunk in stream.body:
                    size += len(chunk)
                    if size > 25_000_000:
                        raise VisualImagePreparationError("visual_image_too_large", "Visual-search image exceeds the byte limit.")
                    chunks.append(chunk)
            return b"".join(chunks)
        content = asyncio.run(read())
        if sha256_fingerprint(content).sha256 != expected_sha256:
            raise SourceAssetContentUnavailable("source content changed before visual indexing")
        return content

    @staticmethod
    def _document(context: JobHandlerContext, provider: VisualSearchElasticsearchIndex) -> VisualIndexDocument | None:
        asset_id = str(context.job.payload.get("asset_id") or context.job.entity_id or "").strip()
        source_asset_id = str(context.job.payload.get("source_asset_id") or "").strip()
        expected = str(context.job.payload.get("content_sha256") or "").strip()
        if not asset_id or not source_asset_id or not expected:
            raise ValueError("visual index job requires asset_id, source_asset_id and content_sha256")
        if context.job.payload.get("embedding_schema_version") != provider.descriptor.embedding_schema_version:
            raise ValueError("visual index job schema is stale")
        with context.dependencies.session_factory() as session:
            row = session.execute(
                select(AssetModel, SourceAssetModel)
                .join(AssetSourceLinkModel, AssetSourceLinkModel.asset_id == AssetModel.id)
                .join(SourceAssetModel, SourceAssetModel.id == AssetSourceLinkModel.source_asset_id)
                .where(
                    AssetModel.tenant_id == context.job.tenant_id,
                    AssetModel.id == asset_id,
                    AssetModel.content_hash == expected,
                    AssetSourceLinkModel.tenant_id == context.job.tenant_id,
                    SourceAssetModel.tenant_id == context.job.tenant_id,
                    SourceAssetModel.id == source_asset_id,
                    SourceAssetModel.deleted_at.is_(None),
                )
            ).one_or_none()
            if row is None:
                return None
            _asset, source = row
            if not is_supported_image_mime_type(source.mime_type):
                return None
            details = SearchSourceIndexResolver(session).for_source(source)
            return VisualIndexDocument(
                tenant_id=context.job.tenant_id, asset_id=asset_id, content_sha256=expected,
                embedding=VisualEmbedding(provider.descriptor, tuple(0.0 for _ in range(provider.descriptor.dimension))),
                source_id=details.source_id or None, source_provider=details.source_provider or None,
                media_kind=details.media_kind or "image", mime_type=details.mime_type or None,
                extension=details.extension or None, ancestor_ids=details.ancestor_ids,
            )
