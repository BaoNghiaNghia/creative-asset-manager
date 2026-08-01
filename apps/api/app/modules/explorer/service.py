import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from urllib.parse import quote

from sqlalchemy import select

from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_metadata.normalizer import MetadataNormalizer
from app.modules.assets.model import AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.assets.status_service import AssetProcessingStatusService

from app.modules.explorer.provider_contract import SourceProviderFactory
from app.modules.explorer.schema import AssetNode, FolderListing, SearchRequest, SearchResponse
from app.modules.explorer.media_types import infer_media_type
from app.modules.metadata.service import MetadataService, schedule_metadata_index
from app.modules.authorization.folder_scope import ViewerFolderAccess

logger = logging.getLogger(__name__)
FOLDER = "application/vnd.google-apps.folder"
ProgressCallback = Callable[[dict], Awaitable[None]]


async def _report(callback: ProgressCallback | None, **event) -> None:
    if callback:
        await callback(event)


MOCK = [
    AssetNode(id="campaigns", name="Campaigns", kind="folder", mime_type=FOLDER, parent_id="root", has_children=True),
    AssetNode(id="brand", name="Brand library", kind="folder", mime_type=FOLDER, parent_id="root", has_children=True),
    AssetNode(id="hero", name="hero-banner.jpg", kind="image", mime_type="image/jpeg", parent_id="campaigns", size=4820000),
    AssetNode(id="film", name="launch-film.mp4", kind="video", mime_type="video/mp4", parent_id="campaigns", size=128420000),
]


class ExplorerService:
    def __init__(
        self,
        provider_factory: SourceProviderFactory,
        asset_status_service: AssetProcessingStatusService | None = None,
        viewer_access: ViewerFolderAccess | None = None,
    ):
        self.provider_factory = provider_factory
        self.asset_status_service = asset_status_service
        self.viewer_access = viewer_access

    def _enrich_asset_identities(
        self,
        items: list[AssetNode],
        tenant_id: str,
        provider: str,
        external_source_id: str | None = None,
    ) -> list[AssetNode]:
        if self.asset_status_service is None:
            return items
        identities = self.asset_status_service.list_source_identities(
            tenant_id,
            provider,
            [item.id for item in items if item.kind != "folder"],
            external_source_id=external_source_id,
        )
        for item in items:
            matches = identities.get(item.id, [])
            if item.kind == "folder" or len(matches) != 1:
                continue
            identity = matches[0]
            item.source_asset_id = identity.source_asset_id
            item.external_source_id = identity.external_source_id
            item.internal_asset_id = identity.internal_asset_id
        return items

    @staticmethod
    def _assign_media_proxy_urls(
        items: list[AssetNode],
        *,
        provider: str,
        external_source_id: str | None,
    ) -> list[AssetNode]:
        """Use tenant-scoped thumbnail/media proxies instead of provider URLs."""
        if not external_source_id:
            return items
        for item in items:
            if item.kind in {"image", "video"}:
                endpoint = "thumbnail" if provider == "google-drive" else "media"
                cache_version = (
                    f"&v={quote(item.modified_at.isoformat(), safe='')}"
                    if item.modified_at
                    else ""
                )
                item.thumbnail_url = (
                    f"/api/explorer/{endpoint}/{quote(item.id, safe='')}"
                    f"?provider={quote(provider, safe='')}"
                    f"&external_source_id={quote(external_source_id, safe='')}"
                    f"{cache_version}"
                )
        return items

    async def list_folder(
        self,
        parent_id: str,
        access_token: str | None,
        account_id: str = "developer",
        provider: str = "google-drive",
        tenant_id: str | None = None,
        external_source_id: str | None = None,
        viewer_parent_authorized: bool = False,
        page_token: str | None = None,
        page_size: int = 100,
    ) -> FolderListing:
        if access_token:
            async with self.provider_factory(provider, access_token) as client:
                parent, page = await asyncio.gather(
                    client.get_node(parent_id),
                    client.list_children_page(
                        parent_id,
                        page_token=page_token,
                        page_size=page_size,
                    ),
                )
                children, next_page_token = page
        elif provider == "google-drive":
            parent = (
                AssetNode(id="root", name="My Drive", kind="folder", mime_type=FOLDER, has_children=True)
                if parent_id == "root"
                else next(item for item in MOCK if item.id == parent_id)
            )
            all_children = [item for item in MOCK if item.parent_id == parent_id]
            start = int(page_token or 0)
            children = all_children[start : start + page_size]
            next_page_token = str(start + page_size) if start + page_size < len(all_children) else None
        else:
            raise PermissionError("Connect SharePoint to browse files.")

        self._enrich_asset_identities(
            children,
            tenant_id or account_id,
            provider,
            external_source_id,
        )
        if self.viewer_access is not None and self.viewer_access.restricted and not viewer_parent_authorized:
            # Root exposes only the explicitly assigned folders. A verified
            # descendant folder passes viewer_parent_authorized=True so every
            # direct child remains visible.
            children = [item for item in children if self.viewer_access.allows(
                item_id=item.id, parent_id=parent_id, ancestor_ids=item.ancestor_ids
            )]
        self._assign_media_proxy_urls(
            children,
            provider=provider,
            external_source_id=external_source_id,
        )

        metadata = MetadataService(account_id, provider)
        schedule_metadata_index(metadata.index_listing(parent, children))
        return FolderListing(
            parent=parent,
            children=children,
            next_page_token=next_page_token,
            has_more=bool(next_page_token),
        )

    async def list_folders(
        self,
        parent_id: str,
        access_token: str | None,
        provider: str = "google-drive",
        viewer_parent_authorized: bool = False,
    ) -> list[AssetNode]:
        if access_token:
            async with self.provider_factory(provider, access_token) as client:
                folders = await client.list_children(parent_id, folders_only=True)
                if self.viewer_access and self.viewer_access.restricted and not viewer_parent_authorized:
                    folders = [folder for folder in folders if self.viewer_access.allows(
                        item_id=folder.id, parent_id=parent_id, ancestor_ids=folder.ancestor_ids,
                    )]
                return folders
        if provider == "sharepoint":
            raise PermissionError("Connect SharePoint to browse folders.")
        folders = [item for item in MOCK if item.parent_id == parent_id and item.kind == "folder"]
        if self.viewer_access and self.viewer_access.restricted and not viewer_parent_authorized:
            folders = [folder for folder in folders if self.viewer_access.allows(
                item_id=folder.id, parent_id=parent_id, ancestor_ids=folder.ancestor_ids,
            )]
        return folders

    def _search_analyzed_assets(
        self,
        body: SearchRequest,
        tenant_id: str | None,
        account_id: str,
    ) -> list[AssetNode]:
        """Search completed PostgreSQL projections when the legacy index has no AI metadata."""
        if self.asset_status_service is None or not tenant_id:
            return []

        source_type = {"google-drive": "google_drive", "sharepoint": "sharepoint"}.get(body.provider)
        if source_type is None:
            return []

        session = self.asset_status_service.session
        sources = list(session.scalars(select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.source_type == source_type,
        )))
        account_source_ids = [
            source.id
            for source in sources
            if str((source.source_metadata or {}).get("provider_account_id") or "") == account_id
        ]
        # Older source records may predate provider-account metadata. Use those
        # only when the current account has no explicit source record.
        source_ids = account_source_ids or [
            source.id
            for source in sources
            if not (source.source_metadata or {}).get("provider_account_id")
        ]
        if not source_ids:
            return []

        source_assets = list(session.scalars(select(SourceAssetModel).where(
            SourceAssetModel.tenant_id == tenant_id,
            SourceAssetModel.external_source_id.in_(source_ids),
            SourceAssetModel.deleted_at.is_(None),
        )))
        parents_by_source_asset = {
            (source.external_source_id, source.external_asset_id): self._source_parents(source)
            for source in source_assets
        }

        rows = session.execute(
            select(AssetAiAnalysisModel, SourceAssetModel)
            .join(AssetSourceLinkModel, AssetSourceLinkModel.asset_id == AssetAiAnalysisModel.asset_id)
            .join(SourceAssetModel, SourceAssetModel.id == AssetSourceLinkModel.source_asset_id)
            .where(
                AssetAiAnalysisModel.tenant_id == tenant_id,
                AssetSourceLinkModel.tenant_id == tenant_id,
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id.in_(source_ids),
                SourceAssetModel.deleted_at.is_(None),
                AssetAiAnalysisModel.status == "completed",
                AssetAiAnalysisModel.search_projection.is_not(None),
            )
            .order_by(
                AssetAiAnalysisModel.asset_id,
                AssetAiAnalysisModel.completed_at.desc(),
                AssetAiAnalysisModel.id.desc(),
            )
        ).all()

        latest_by_asset: dict[str, str] = {}
        for analysis, _source in rows:
            if (
                analysis.asset_id not in latest_by_asset
                and isinstance(analysis.search_projection, dict)
                and not analysis.validation_errors_json
            ):
                latest_by_asset[analysis.asset_id] = analysis.id

        normalized_query = MetadataNormalizer.normalize_text(body.query)
        query_tokens = set(normalized_query.split())
        matches: list[tuple[int, AssetNode]] = []
        seen_assets: set[str] = set()
        for analysis, source in rows:
            if latest_by_asset.get(analysis.asset_id) != analysis.id or analysis.asset_id in seen_assets:
                continue
            if not self._is_in_search_scope(
                source, body.root_id, parents_by_source_asset,
            ):
                continue
            searchable = self._projection_search_text(analysis.search_projection or {})
            searchable_tokens = set(MetadataNormalizer.normalize_text(searchable).split())
            if not query_tokens or not query_tokens.issubset(searchable_tokens):
                continue
            seen_assets.add(analysis.asset_id)
            mime_type = infer_media_type(source.filename, source.mime_type)
            kind = (
                "image" if mime_type.startswith("image/") else
                "video" if mime_type.startswith("video/") else
                "pdf" if mime_type == "application/pdf" else
                "document" if "document" in mime_type else "other"
            )
            parent_ids = self._source_parents(source)
            score = len(query_tokens) + (100 if normalized_query in MetadataNormalizer.normalize_text(searchable) else 0)
            matches.append((score, AssetNode(
                id=source.external_asset_id,
                internal_asset_id=analysis.asset_id,
                source_asset_id=source.id,
                external_source_id=source.external_source_id,
                provider=body.provider,
                name=source.filename or "Untitled",
                kind=kind,
                mime_type=mime_type,
                parent_id=parent_ids[0] if parent_ids else None,
                ancestor_ids=parent_ids,
                size=source.size_bytes,
                modified_at=source.source_modified_at,
                has_children=False,
            )))

        matches.sort(key=lambda item: (-item[0], item[1].name.casefold(), item[1].id))
        return [item for _, item in matches[:body.limit]]

    @staticmethod
    def _source_parents(source: SourceAssetModel) -> list[str]:
        metadata = source.source_metadata or {}
        parents = metadata.get("parents")
        if isinstance(parents, list):
            return [str(parent) for parent in parents if parent]
        parent_id = metadata.get("parent_id")
        return [str(parent_id)] if parent_id else []

    @classmethod
    def _is_in_search_scope(
        cls, source: SourceAssetModel, root_id: str,
        parents_by_source_asset: dict[tuple[str, str], list[str]],
    ) -> bool:
        if root_id in {"root", "sharepoint-root"}:
            return True
        queue = cls._source_parents(source)
        visited: set[str] = set()
        while queue:
            parent_id = queue.pop()
            if parent_id == root_id:
                return True
            if parent_id in visited:
                continue
            visited.add(parent_id)
            queue.extend(parents_by_source_asset.get((source.external_source_id, parent_id), ()))
        return False

    @staticmethod
    def _projection_search_text(projection: dict) -> str:
        values: list[str] = []
        for key in ("search_text", "search_terms", "normalized_terms", "phrases", "numbers"):
            value = projection.get(key)
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(str(item) for item in value if isinstance(item, (str, int, float)))
        for item in projection.get("path_values") or []:
            if isinstance(item, dict) and isinstance(item.get("value"), (str, int, float)):
                values.append(str(item["value"]))
        return " ".join(values)


    async def search_subtree(
        self,
        body: SearchRequest,
        access_token: str | None,
        account_id: str,
        progress: ProgressCallback | None = None,
        tenant_id: str | None = None,
        external_source_id: str | None = None,
    ) -> SearchResponse:
        metadata = MetadataService(account_id, body.provider)
        await _report(progress, status="Reading metadata index", progress=3, processed_folders=0, pending_folders=0)
        rows = await metadata.list_subtree(body.root_id)
        by_item = {row["item_id"]: row for row in rows}
        max_items = int(os.getenv("DIRECTUS_METADATA_MAX_ITEMS", "20000"))
        concurrency = max(1, int(os.getenv("DIRECTUS_METADATA_CONCURRENCY", "6")))
        truncated = False
        skipped_folders = 0
        await _report(
            progress,
            status=f"Loaded {max(0, len(by_item) - 1)} indexed items",
            progress=8,
            indexed_count=max(0, len(by_item) - 1),
            processed_folders=0,
            pending_folders=0,
        )

        if access_token:
            async with self.provider_factory(body.provider, access_token) as client:
                root_row = by_item.get(body.root_id)
                if not root_row:
                    root_asset = await client.get_node(body.root_id)
                    root_row = metadata.make_row(
                        root_asset,
                        body.ancestor_ids,
                        body.ancestor_names,
                    )
                    await metadata.upsert([root_row])
                    by_item[body.root_id] = root_row

                queued: set[str] = set()
                queue = [
                    row
                    for row in by_item.values()
                    if row.get("kind") == "folder" and metadata.needs_refresh(row)
                ]
                queued.update(row["item_id"] for row in queue)
                processed_folders = 0
                progress_percent = 10
                await _report(
                    progress,
                    status="Scanning Drive folders" if queue else "Metadata index is up to date",
                    progress=10 if queue else 92,
                    indexed_count=max(0, len(by_item) - 1),
                    processed_folders=0,
                    pending_folders=len(queue),
                )

                while queue and len(by_item) < max_items:
                    batch, queue = queue[:concurrency], queue[concurrency:]
                    results = await asyncio.gather(
                        *(client.list_children(row["item_id"]) for row in batch),
                        return_exceptions=True,
                    )

                    for batch_index, (parent_row, result) in enumerate(zip(batch, results)):
                        if isinstance(result, Exception):
                            if parent_row["item_id"] == body.root_id:
                                raise result

                            skipped_folders += 1
                            processed_folders += 1
                            remaining_folders = len(queue) + len(batch) - batch_index - 1
                            known_folders = processed_folders + remaining_folders
                            percent = max(
                                progress_percent,
                                min(90, 10 + round(80 * processed_folders / max(known_folders, 1))),
                            )
                            progress_percent = percent
                            logger.warning(
                                "Skipping inaccessible Drive folder %s during metadata indexing: %s",
                                parent_row["item_id"],
                                type(result).__name__,
                            )
                            parent_asset = metadata.to_asset(parent_row)
                            await metadata.upsert([
                                metadata.make_row(
                                    parent_asset,
                                    list(parent_row.get("ancestor_ids") or []),
                                    list(parent_row.get("ancestor_names") or []),
                                    children_indexed=True,
                                )
                            ])
                            await _report(
                                progress,
                                status=f"Skipped {skipped_folders} inaccessible folders",
                                progress=percent,
                                indexed_count=max(0, len(by_item) - 1),
                                processed_folders=processed_folders,
                                pending_folders=remaining_folders,
                                skipped_folders=skipped_folders,
                            )
                            continue

                        remaining = max_items - len(by_item)
                        children = result[:remaining]
                        if len(children) < len(result):
                            truncated = True

                        ancestor_ids = [
                            *list(parent_row.get("ancestor_ids") or []),
                            parent_row["item_id"],
                        ]
                        ancestor_names = [
                            *list(parent_row.get("ancestor_names") or []),
                            parent_row.get("name") or "Folder",
                        ]
                        parent_asset = metadata.to_asset(parent_row)
                        indexed_parent = metadata.make_row(
                            parent_asset,
                            list(parent_row.get("ancestor_ids") or []),
                            list(parent_row.get("ancestor_names") or []),
                            children_indexed=True,
                        )
                        child_rows = [
                            metadata.make_row(child, ancestor_ids, ancestor_names)
                            for child in children
                        ]

                        prepared_children = []
                        for child_row in child_rows:
                            existing = by_item.get(child_row["item_id"])
                            if existing and existing.get("children_indexed"):
                                child_row["children_indexed"] = True
                                child_row["indexed_at"] = existing.get("indexed_at")
                            by_item[child_row["item_id"]] = child_row
                            prepared_children.append(child_row)

                            if (
                                child_row.get("kind") == "folder"
                                and child_row["item_id"] not in queued
                                and metadata.needs_refresh(child_row)
                            ):
                                queue.append(child_row)
                                queued.add(child_row["item_id"])

                        by_item[indexed_parent["item_id"]] = indexed_parent
                        await metadata.upsert([indexed_parent, *prepared_children])
                        processed_folders += 1
                        remaining_folders = len(queue) + len(batch) - batch_index - 1
                        known_folders = processed_folders + remaining_folders
                        percent = max(
                            progress_percent,
                            min(90, 10 + round(80 * processed_folders / max(known_folders, 1))),
                        )
                        progress_percent = percent
                        await _report(
                            progress,
                            status=f"Indexed {processed_folders} of at least {known_folders} folders",
                            progress=percent,
                            indexed_count=max(0, len(by_item) - 1),
                            processed_folders=processed_folders,
                            pending_folders=remaining_folders,
                            skipped_folders=skipped_folders,
                        )

                        if truncated:
                            queue = []
                            break
        else:
            if body.provider == "sharepoint":
                raise PermissionError("Connect SharePoint before indexing metadata.")
            await _report(progress, status="Indexing demo metadata", progress=20, processed_folders=0, pending_folders=1)
            root = by_item.get(body.root_id)
            if not root:
                root_asset = (
                    AssetNode(id="root", name="My Drive", kind="folder", mime_type=FOLDER, has_children=True)
                    if body.root_id == "root"
                    else next(item for item in MOCK if item.id == body.root_id)
                )
                root = metadata.make_row(root_asset, body.ancestor_ids, body.ancestor_names)
                by_item[body.root_id] = root

            queue = [root]
            processed_folders = 0
            while queue:
                parent_row = queue.pop(0)
                children = [item for item in MOCK if item.parent_id == parent_row["item_id"]]
                ancestor_ids = [*list(parent_row.get("ancestor_ids") or []), parent_row["item_id"]]
                ancestor_names = [*list(parent_row.get("ancestor_names") or []), parent_row["name"]]
                for child in children:
                    row = metadata.make_row(child, ancestor_ids, ancestor_names)
                    by_item[child.id] = row
                    if child.kind == "folder":
                        queue.append(row)
                processed_folders += 1
                known_folders = processed_folders + len(queue)
                await _report(
                    progress,
                    status=f"Indexed {processed_folders} of at least {known_folders} folders",
                    progress=min(90, 20 + round(70 * processed_folders / max(known_folders, 1))),
                    indexed_count=max(0, len(by_item) - 1),
                    processed_folders=processed_folders,
                    pending_folders=len(queue),
                )
            await metadata.upsert(list(by_item.values()))

        indexed_rows = list(by_item.values())
        await _report(
            progress,
            status="Searching indexed metadata",
            progress=95,
            indexed_count=max(0, len(indexed_rows) - 1),
            processed_folders=processed_folders,
            pending_folders=0,
            skipped_folders=skipped_folders,
        )
        items = metadata.search(indexed_rows, body.query, body.root_id, body.limit)
        projected_items = self._search_analyzed_assets(body, tenant_id, account_id)
        if projected_items:
            legacy_ids = {item.id for item in projected_items}
            items = [*projected_items, *(item for item in items if item.id not in legacy_ids)][:body.limit]
        indexed_count = max(0, len(indexed_rows) - 1, len(projected_items))
        self._enrich_asset_identities(
            items,
            tenant_id or account_id,
            body.provider,
            external_source_id,
        )
        if self.viewer_access is not None and self.viewer_access.restricted:
            items = [item for item in items if self.viewer_access.allows(
                item_id=item.id, parent_id=item.parent_id, ancestor_ids=item.ancestor_ids
            )]
        self._assign_media_proxy_urls(
            items,
            provider=body.provider,
            external_source_id=external_source_id,
        )
        return SearchResponse(
            items=items,
            indexed_count=indexed_count,
            index_source=metadata.source,
            truncated=truncated,
            skipped_folders=skipped_folders,
        )
