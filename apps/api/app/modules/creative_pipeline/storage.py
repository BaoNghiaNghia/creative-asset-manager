from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StorageItem:
    id: str
    name: str
    parent_id: str | None
    kind: str = "folder"


class PipelineStorageError(RuntimeError):
    pass


class PipelineStorageAmbiguous(PipelineStorageError):
    pass


class PipelineStorageUnsupported(PipelineStorageError):
    pass


class CreativePipelineStorageGateway(Protocol):
    supports_writes: bool
    async def get_item(self, item_id: str) -> StorageItem | None: ...
    async def list_children(self, parent_id: str) -> list[StorageItem]: ...
    async def create_folder(self, parent_id: str, name: str) -> StorageItem: ...
    async def upload_bytes(self, parent_id: str, name: str, mime_type: str, content: bytes) -> StorageItem: ...
    async def upload_file(self, parent_id: str, name: str, mime_type: str, local_path: str | Path) -> StorageItem: ...
    async def rename_item(self, item_id: str, name: str) -> StorageItem: ...
    async def delete_item(self, item_id: str) -> None: ...
    async def download_bytes(self, item_id: str) -> bytes: ...


class ExplorerStorageGateway:
    """Normalized write adapter around an existing authenticated source adapter."""
    supports_writes = True

    def __init__(self, provider):
        self.provider = provider

    async def get_item(self, item_id):
        node = await self.provider.get_node(item_id)
        return StorageItem(node.id, node.name, node.parent_id, node.kind)

    async def list_children(self, parent_id):
        nodes = await self.provider.list_children(parent_id)
        return [StorageItem(n.id, n.name, n.parent_id, n.kind) for n in nodes]

    async def create_folder(self, parent_id, name):
        method = getattr(self.provider, "create_folder", None)
        if method is None:
            raise PipelineStorageUnsupported("pipeline_storage_write_unsupported")
        node = await method(parent_id, name)
        return StorageItem(node.id, node.name, node.parent_id, node.kind)

    async def upload_bytes(self, parent_id, name, mime_type, content):
        method = getattr(self.provider, "upload_file", None)
        if method is None:
            raise PipelineStorageUnsupported("pipeline_storage_write_unsupported")
        node = await method(parent_id, name, mime_type, content)
        return StorageItem(node.id, node.name, node.parent_id, getattr(node, "kind", "other"))

    async def upload_file(self, parent_id, name, mime_type, local_path):
        stream_method = getattr(self.provider, "upload_file_stream", None)
        if stream_method is not None:
            async def chunks():
                with open(local_path, "rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        yield chunk
            node = await stream_method(parent_id, name, mime_type, chunks())
            return StorageItem(node.id, node.name, node.parent_id, getattr(node, "kind", "other"))
        method = getattr(self.provider, "upload_file", None)
        if method is None:
            raise PipelineStorageUnsupported("pipeline_storage_write_unsupported")
        with open(local_path, "rb") as handle:
            content = handle.read()
        node = await method(parent_id, name, mime_type, content)
        return StorageItem(node.id, node.name, node.parent_id, getattr(node, "kind", "other"))

    async def rename_item(self, item_id, name):
        method = getattr(self.provider, "rename_file", None)
        if method is None:
            raise PipelineStorageUnsupported("pipeline_storage_write_unsupported")
        node = await method(item_id, name)
        return StorageItem(node.id, node.name, node.parent_id, node.kind)

    async def delete_item(self, item_id):
        method = getattr(self.provider, "delete_file", None)
        if method is None:
            raise PipelineStorageUnsupported("pipeline_storage_write_unsupported")
        await method(item_id)

    async def download_bytes(self, item_id):
        method = getattr(self.provider, "download_file", None) or getattr(self.provider, "download_bytes", None)
        if method is None:
            raise PipelineStorageUnsupported("pipeline_storage_read_unsupported")
        result = method(item_id)
        return await result if hasattr(result, "__await__") else result


CANONICAL_CHILDREN = ("Input", "Idea Story", "Prompt", "Generating", "Video Output", "Watermark & Smart Enhance", "Logs")


async def ensure_pipeline_structure(listing, gateway: CreativePipelineStorageGateway):
    if not gateway.supports_writes:
        raise PipelineStorageUnsupported("pipeline_storage_write_unsupported")
    children = await gateway.list_children(listing.external_folder_id)
    existing = [item for item in children if item.name == "Pipeline" and item.kind == "folder"]
    if listing.pipeline_folder_id:
        pipeline = await gateway.get_item(listing.pipeline_folder_id)
        if pipeline is None:
            raise PipelineStorageError("pipeline_folder_missing")
        if pipeline.kind != "folder" or pipeline.parent_id != listing.external_folder_id or pipeline.name != "Pipeline":
            raise PipelineStorageError("pipeline_folder_identity_invalid")
    else:
        if len(existing) > 1:
            raise PipelineStorageAmbiguous("pipeline_folder_ambiguous")
        pipeline = existing[0] if existing else await gateway.create_folder(listing.external_folder_id, "Pipeline")
        listing.pipeline_folder_id = pipeline.id
    children = await gateway.list_children(pipeline.id)
    folders = {}
    for name in CANONICAL_CHILDREN:
        matches = [item for item in children if item.name == name and item.kind == "folder"]
        if len(matches) > 1:
            raise PipelineStorageAmbiguous(f"pipeline_folder_ambiguous:{name}")
        folders[name] = matches[0] if matches else await gateway.create_folder(pipeline.id, name)
    prompt_children = await gateway.list_children(folders["Prompt"].id)
    for name in ("seedance", "google_omni"):
        matches = [item for item in prompt_children if item.name == name and item.kind == "folder"]
        if len(matches) > 1:
            raise PipelineStorageAmbiguous(f"pipeline_folder_ambiguous:Prompt/{name}")
        if not matches:
            await gateway.create_folder(folders["Prompt"].id, name)
    ratio_names = ("1x1",) if listing.platform == "etsy" else ("16x9", "9x16")
    for parent_name in ("Video Output", "Watermark & Smart Enhance"):
        parent = folders[parent_name]
        direct = await gateway.list_children(parent.id)
        for name in ratio_names:
            matches = [item for item in direct if item.name == name and item.kind == "folder"]
            if len(matches) > 1:
                raise PipelineStorageAmbiguous(f"pipeline_folder_ambiguous:{parent_name}/{name}")
            if not matches:
                await gateway.create_folder(parent.id, name)
    return pipeline
