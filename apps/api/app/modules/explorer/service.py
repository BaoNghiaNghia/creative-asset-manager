import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from app.modules.explorer.provider_contract import SourceProviderFactory
from app.modules.explorer.schema import AssetNode, FolderListing, SearchRequest, SearchResponse
from app.modules.metadata.service import MetadataService, schedule_metadata_index

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
    def __init__(self, provider_factory: SourceProviderFactory):
        self.provider_factory = provider_factory

    async def list_folder(
        self,
        parent_id: str,
        access_token: str | None,
        account_id: str = "developer",
        provider: str = "google-drive",
    ) -> FolderListing:
        if access_token:
            async with self.provider_factory(provider, access_token) as client:
                parent, children = await asyncio.gather(
                    client.get_node(parent_id),
                    client.list_children(parent_id),
                )
        elif provider == "google-drive":
            parent = (
                AssetNode(id="root", name="My Drive", kind="folder", mime_type=FOLDER, has_children=True)
                if parent_id == "root"
                else next(item for item in MOCK if item.id == parent_id)
            )
            children = [item for item in MOCK if item.parent_id == parent_id]
        else:
            raise PermissionError("Connect SharePoint to browse files.")

        metadata = MetadataService(account_id, provider)
        schedule_metadata_index(metadata.index_listing(parent, children))
        return FolderListing(parent=parent, children=children)

    async def list_folders(
        self,
        parent_id: str,
        access_token: str | None,
        provider: str = "google-drive",
    ) -> list[AssetNode]:
        if access_token:
            async with self.provider_factory(provider, access_token) as client:
                return await client.list_children(parent_id, folders_only=True)
        if provider == "sharepoint":
            raise PermissionError("Connect SharePoint to browse folders.")
        return [item for item in MOCK if item.parent_id == parent_id and item.kind == "folder"]

    async def search_subtree(
        self,
        body: SearchRequest,
        access_token: str | None,
        account_id: str,
        progress: ProgressCallback | None = None,
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
        return SearchResponse(
            items=metadata.search(indexed_rows, body.query, body.root_id, body.limit),
            indexed_count=max(0, len(indexed_rows) - 1),
            index_source=metadata.source,
            truncated=truncated,
            skipped_folders=skipped_folders,
        )
