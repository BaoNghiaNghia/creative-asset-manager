from __future__ import annotations
import hashlib
import os
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from app.modules.creative_pipeline.storage import (
    DownloadedArtifactInfo, PipelineStorageError, PipelineStorageUnsupported,
)

@dataclass(frozen=True, slots=True)
class StagedVideoArtifact:
    path: str
    size_bytes: int
    content_hash: str
    first_bytes: bytes
    content_type: str | None = None

@asynccontextmanager
async def stage_video_artifact_to_temp(gateway, artifact, max_bytes: int):
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if getattr(artifact, "size_bytes", None) is not None and artifact.size_bytes > max_bytes:
        raise PipelineStorageError("creative_video_input_too_large")
    fd, path = tempfile.mkstemp(prefix="cp-video-input-", suffix=".mp4")
    os.close(fd)
    try:
        info = None
        download = getattr(gateway, "download_to_file", None)
        if download is not None:
            try:
                info = await download(artifact.external_file_id, path, maximum_bytes=max_bytes)
            except PipelineStorageUnsupported:
                info = None
        if info is None:
            # Explicit compatibility path for byte-only gateways. The hard limit
            # is enforced before writing; streaming adapters never enter here.
            content = await gateway.download_bytes(artifact.external_file_id)
            if not isinstance(content, (bytes, bytearray, memoryview)):
                raise PipelineStorageError("creative_video_input_unavailable")
            content = bytes(content)
            if len(content) > max_bytes:
                raise PipelineStorageError("creative_video_input_too_large")
            with open(path, "wb") as handle:
                handle.write(content)
            info = DownloadedArtifactInfo(
                len(content), hashlib.sha256(content).hexdigest(), content[:64], "video/mp4"
            )
        if info.size_bytes <= 0 or info.size_bytes > max_bytes:
            raise PipelineStorageError("creative_video_input_too_large" if info.size_bytes > max_bytes else "creative_video_input_unsupported")
        if artifact.size_bytes is not None and info.size_bytes != artifact.size_bytes:
            raise PipelineStorageError("creative_raw_artifact_inconsistent")
        if artifact.content_hash and info.sha256.lower() != artifact.content_hash.lower():
            raise PipelineStorageError("creative_raw_artifact_inconsistent")
        if artifact.mime_type and artifact.mime_type != "video/mp4":
            raise PipelineStorageError("creative_raw_artifact_inconsistent")
        if b"ftyp" not in info.first_bytes:
            raise PipelineStorageError("creative_raw_artifact_inconsistent")
        yield StagedVideoArtifact(path, info.size_bytes, info.sha256, info.first_bytes, info.content_type)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
