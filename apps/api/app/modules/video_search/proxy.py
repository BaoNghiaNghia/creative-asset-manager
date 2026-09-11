from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.assets.content_resolver import (
    SourceAssetContentResolver,
    SourceAssetContentTransient,
    SourceAssetContentUnavailable,
)
from app.modules.assets.model import SourceAssetModel
from app.modules.pipeline.mime_types import is_supported_video_mime_type
from app.modules.video_search.fingerprint import build_video_source_fingerprint


logger = logging.getLogger(__name__)


class VideoProxyPreparationError(RuntimeError):
    """Base error for local, ephemeral video proxy preparation."""


class VideoProxyConfigurationError(VideoProxyPreparationError):
    pass


class VideoProxyStorageError(VideoProxyPreparationError):
    pass


class VideoProxySourceChangedError(VideoProxyPreparationError):
    pass


class VideoProxyChunkTooLargeError(VideoProxyPreparationError):
    pass


class VideoProxyProcessError(VideoProxyPreparationError):
    def __init__(
        self,
        message: str,
        *,
        phase: str = "transcode",
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.returncode = returncode


class VideoProxySourceError(VideoProxyPreparationError):
    pass


class VideoProxySourceTemporarilyUnavailable(VideoProxyPreparationError):
    pass


class VideoProxySourceEmptyError(VideoProxySourceError):
    pass


class VideoProxySourceSizeMismatchError(VideoProxySourceError):
    pass


class VideoProxySourceTooLargeError(VideoProxySourceError):
    pass


class VideoProxySourceStreamError(VideoProxySourceError):
    pass


class VideoProxyDiskSpaceError(VideoProxyStorageError):
    pass


class VideoProxyMaterializationError(VideoProxyStorageError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedVideoChunk:
    chunk_index: int
    path: Path
    source_start_ms: int
    source_end_ms: int
    duration_ms: int
    size_bytes: int
    width: int | None
    height: int | None


ProcessFactory = Callable[..., Awaitable[Any]]
DiskUsageProvider = Callable[[str | os.PathLike[str]], Any]


class VideoProxyPreparationService:
    """Materialize one provider stream in an owned ephemeral directory, then transcode it.

    The seekable source and derived MP4 chunks live only under VIDEO_TEMP_DIRECTORY
    and are removed together after analysis completes or fails.
    """

    _STDERR_TAIL_BYTES = 16 * 1024
    _TERMINATE_TIMEOUT_SECONDS = 5
    _STORAGE_POLL_SECONDS = 0.25
    _WORKING_RESERVE_BYTES = 64 * 1024 * 1024

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        *,
        content_resolver: SourceAssetContentResolver | None = None,
        create_subprocess_exec: ProcessFactory = asyncio.create_subprocess_exec,
        disk_usage: DiskUsageProvider = shutil.disk_usage,
        storage_poll_seconds: float | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._resolver = content_resolver or SourceAssetContentResolver(session_factory)
        self._create_subprocess_exec = create_subprocess_exec
        self._disk_usage = disk_usage
        self._storage_poll_seconds = storage_poll_seconds or self._STORAGE_POLL_SECONDS

    async def prepare(
        self, *, tenant_id: str, source_asset_id: str, expected_source_fingerprint: str
    ) -> tuple[PreparedVideoChunk, ...]:
        source_asset = self._load_source_asset(tenant_id, source_asset_id)
        if source_asset is None or not is_supported_video_mime_type(source_asset.mime_type):
            raise VideoProxySourceChangedError("source asset is unavailable or not a supported video")
        if build_video_source_fingerprint(source_asset) != expected_source_fingerprint:
            raise VideoProxySourceChangedError("source asset fingerprint changed before proxy preparation")

        root = self._configured_root()
        self.cleanup_stale()
        source_limit = self._max_source_bytes()
        expected_source_size = self._expected_source_size(source_asset, source_limit)
        output_reserve = self._output_storage_requirement()
        self._ensure_free_space(root, self._preflight_required_free_space(expected_source_size, output_reserve))
        working_directory = Path(tempfile.mkdtemp(prefix="video-proxy-", dir=root))
        source_path = self._source_path(working_directory, source_asset.mime_type)
        process: Any | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        storage_task: asyncio.Task[None] | None = None
        stop_monitor = asyncio.Event()
        storage_failed = asyncio.Event()
        try:
            await self._materialize_source(
                tenant_id=tenant_id,
                source_asset_id=source_asset_id,
                source_path=source_path,
                expected_size=expected_source_size,
                maximum_size=source_limit,
                root=root,
                output_reserve=output_reserve,
                size_is_authoritative=source_asset.size_bytes is not None,
            )
            await self._probe_source(source_path)
            command = self._ffmpeg_command(working_directory, source_path)
            try:
                process = await self._create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise VideoProxyConfigurationError("FFmpeg executable is unavailable") from exc
            if process.stderr is None:
                raise VideoProxyProcessError(
                    "FFmpeg did not expose a stderr pipe", phase="transcode"
                )
            stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
            storage_task = asyncio.create_task(
                self._monitor_storage(process, root, output_reserve, stop_monitor, storage_failed)
            )
            await process.wait()
            if storage_failed.is_set():
                raise VideoProxyDiskSpaceError("insufficient free space while creating video proxy")
            await stderr_task
            stderr_task = None
            if process.returncode != 0:
                logger.warning(
                    "video_proxy_process_failed",
                    extra={"phase": "transcode", "returncode": process.returncode},
                )
                raise VideoProxyProcessError(
                    "FFmpeg could not transcode the video source",
                    phase="transcode",
                    returncode=process.returncode,
                )
            chunks = await self._probe_chunks(working_directory)
            logger.info(
                "video_proxy_transcode_completed",
                extra={"phase": "transcode", "chunk_count": len(chunks)},
            )
            if self._load_fingerprint(tenant_id, source_asset_id) != expected_source_fingerprint:
                raise VideoProxySourceChangedError("source asset fingerprint changed during proxy preparation")
            return chunks
        except asyncio.CancelledError:
            if process is not None:
                await self._terminate_process(process)
            self._remove_working_directory(working_directory)
            raise
        except Exception:
            if process is not None:
                await self._terminate_process(process)
            self._remove_working_directory(working_directory)
            raise
        finally:
            stop_monitor.set()
            if storage_task is not None:
                await self._cancel_task(storage_task)
            if stderr_task is not None:
                await self._cancel_task(stderr_task)

    async def _materialize_source(
        self,
        *,
        tenant_id: str,
        source_asset_id: str,
        source_path: Path,
        expected_size: int,
        maximum_size: int,
        root: Path,
        output_reserve: int,
        size_is_authoritative: bool,
    ) -> None:
        written = 0
        attempts = max(
            1, int(getattr(self._settings, "VIDEO_PROXY_SOURCE_DOWNLOAD_ATTEMPTS", 3))
        )
        for attempt in range(attempts):
            range_header = f"bytes={written}-" if written else None
            try:
                async with self._resolver.open(
                    tenant_id=tenant_id,
                    source_asset_id=source_asset_id,
                    range_header=range_header,
                ) as stream:
                    # A resumed response must be partial. If a provider ignores
                    # Range, discard the partial file and restart safely instead
                    # of appending duplicate bytes to the video.
                    if written and stream.status_code != 206:
                        source_path.unlink(missing_ok=True)
                        written = 0
                        continue
                    mode = "ab" if written else "xb"
                    with source_path.open(mode) as destination:
                        async for block in stream.body:
                            if not isinstance(block, bytes):
                                raise VideoProxySourceStreamError(
                                    "provider stream emitted a non-bytes block"
                                )
                            next_size = written + len(block)
                            if next_size > maximum_size:
                                raise VideoProxySourceTooLargeError(
                                    "video source exceeds configured maximum size"
                                )
                            self._ensure_free_space(root, output_reserve)
                            destination.write(block)
                            written = next_size
                break
            except VideoProxyPreparationError:
                raise
            except SourceAssetContentTransient as exc:
                if attempt + 1 >= attempts:
                    raise VideoProxySourceTemporarilyUnavailable(
                        "video source provider is temporarily unavailable"
                    ) from exc
                await asyncio.sleep(min(0.5 * (2**attempt), 3.0))
            except SourceAssetContentUnavailable as exc:
                raise VideoProxySourceError("video source content is unavailable") from exc
            except OSError as exc:
                raise VideoProxyMaterializationError(
                    "cannot materialize video source locally"
                ) from exc
        else:
            raise VideoProxySourceTemporarilyUnavailable(
                "video source provider did not support a resumable download"
            )
        if written <= 0:
            raise VideoProxySourceEmptyError("video source is empty")
        if size_is_authoritative and written != expected_size:
            raise VideoProxySourceSizeMismatchError(
                "video source size does not match source metadata"
            )

    @staticmethod
    def _source_path(directory: Path, mime_type: str | None) -> Path:
        suffix = ".mov" if mime_type == "video/quicktime" else ".mp4"
        return directory / ("source" + suffix)
    def _configured_root(self) -> Path:
        raw = getattr(self._settings, "VIDEO_TEMP_DIRECTORY", "")
        if not isinstance(raw, str) or not raw.strip():
            raise VideoProxyConfigurationError("VIDEO_TEMP_DIRECTORY must be configured")
        root = Path(raw).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise VideoProxyConfigurationError("VIDEO_TEMP_DIRECTORY cannot be created") from exc
        if not root.is_dir() or not os.access(root, os.W_OK | os.X_OK):
            raise VideoProxyConfigurationError("VIDEO_TEMP_DIRECTORY is not writable")
        return root.resolve()

    def _max_source_bytes(self) -> int:
        configured = getattr(self._settings, "VIDEO_PROXY_MAX_SOURCE_BYTES", self._settings.VIDEO_PROXY_MAX_CHUNK_BYTES)
        maximum = int(configured)
        if maximum <= 0:
            raise VideoProxyConfigurationError("VIDEO_PROXY_MAX_SOURCE_BYTES must be positive")
        return maximum

    def _storage_safety_reserve(self) -> int:
        maximum = int(self._settings.VIDEO_PROXY_MAX_CHUNK_BYTES)
        if maximum <= 0:
            raise VideoProxyConfigurationError("VIDEO_PROXY_MAX_CHUNK_BYTES must be positive")
        return min(maximum, self._WORKING_RESERVE_BYTES)

    def _output_storage_requirement(self) -> int:
        maximum = int(self._settings.VIDEO_PROXY_MAX_CHUNK_BYTES)
        if maximum <= 0:
            raise VideoProxyConfigurationError("VIDEO_PROXY_MAX_CHUNK_BYTES must be positive")
        return maximum + self._storage_safety_reserve()

    def _expected_source_size(self, source: SourceAssetModel, maximum: int) -> int:
        size = source.size_bytes
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            if size > maximum:
                raise VideoProxySourceTooLargeError("video source exceeds configured maximum size")
            return size
        return maximum

    def _preflight_required_free_space(
        self, expected_source_size: int | None = None, output_requirement: int | None = None
    ) -> int:
        source_bytes = self._max_source_bytes() if expected_source_size is None else expected_source_size
        output_bytes = self._output_storage_requirement() if output_requirement is None else output_requirement
        if source_bytes < 0 or output_bytes < 0:
            raise VideoProxyConfigurationError("video proxy storage requirements must be non-negative")
        return source_bytes + output_bytes

    def _ensure_free_space(self, root: Path, required: int) -> None:
        try:
            free = int(self._disk_usage(root).free)
        except OSError as exc:
            raise VideoProxyDiskSpaceError("cannot inspect free proxy storage") from exc
        if free < required:
            raise VideoProxyDiskSpaceError("insufficient free space for video proxy preparation")

    def _load_source_asset(self, tenant_id: str, source_asset_id: str) -> SourceAssetModel | None:
        with self._session_factory() as session:
            return session.scalar(select(SourceAssetModel).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.id == source_asset_id,
            ))

    def _load_fingerprint(self, tenant_id: str, source_asset_id: str) -> str | None:
        asset = self._load_source_asset(tenant_id, source_asset_id)
        return build_video_source_fingerprint(asset) if asset is not None else None

    def _ffmpeg_command(self, directory: Path, source_path: Path | None = None) -> tuple[str, ...]:
        max_width = int(self._settings.VIDEO_PROXY_MAX_WIDTH)
        max_height = int(self._settings.VIDEO_PROXY_MAX_HEIGHT)
        fps = int(self._settings.VIDEO_PROXY_FPS)
        chunk_seconds = int(self._settings.VIDEO_CHUNK_SECONDS)
        if min(max_width, max_height, fps, chunk_seconds) <= 0:
            raise VideoProxyConfigurationError("video proxy dimensions, FPS, and chunk duration must be positive")
        video_bitrate = int(self._settings.VIDEO_PROXY_VIDEO_BITRATE_KBPS)
        audio_bitrate = int(self._settings.VIDEO_PROXY_AUDIO_BITRATE_KBPS)
        if min(video_bitrate, audio_bitrate) <= 0:
            raise VideoProxyConfigurationError("video proxy bitrates must be positive")
        scale = (
            f"scale=w='min(iw,{max_width})':h='min(ih,{max_height})':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p"
        )
        force_keyframes = f"expr:gte(t,n_forced*{chunk_seconds})"
        return (
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-fflags", "+genpts", "-i", str(source_path or directory / "source.mp4"),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-map_metadata", "-1", "-map_chapters", "-1",
            "-vf", scale, "-r", str(fps), "-fps_mode", "cfr",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-b:v", f"{video_bitrate}k",
            "-c:a", "aac", "-b:a", f"{audio_bitrate}k", "-ar", "48000", "-ac", "2",
            "-avoid_negative_ts", "make_zero", "-max_muxing_queue_size", "2048",
            "-force_key_frames", force_keyframes,
            "-f", "segment", "-segment_time", str(chunk_seconds),
            "-segment_format", "mp4", "-segment_format_options", "movflags=+faststart",
            "-reset_timestamps", "1",
            str(directory / "chunk_%05d.mp4"),
        )

    async def _drain_stderr(self, reader: Any) -> bytes:
        tail = bytearray()
        while True:
            block = await reader.read(4096)
            if not block:
                break
            tail.extend(block)
            if len(tail) > self._STDERR_TAIL_BYTES:
                del tail[:-self._STDERR_TAIL_BYTES]
        return bytes(tail)

    async def _monitor_storage(self, process: Any, root: Path, reserve: int, stop: asyncio.Event, failed: asyncio.Event) -> None:
        while not stop.is_set() and process.returncode is None:
            try:
                self._ensure_free_space(root, reserve)
            except VideoProxyStorageError:
                failed.set()
                await self._terminate_process(process)
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._storage_poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def _probe_source(self, path: Path) -> None:
        """Validate the fully materialized provider download before transcoding."""
        document = await self._ffprobe_document(path, phase="source_probe")
        try:
            duration_seconds = float(document["format"]["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VideoProxyProcessError(
                "ffprobe returned malformed source metadata", phase="source_probe"
            ) from exc
        video = next(
            (
                stream
                for stream in document.get("streams", [])
                if isinstance(stream, dict) and stream.get("codec_type") == "video"
            ),
            None,
        )
        if not math.isfinite(duration_seconds) or duration_seconds <= 0 or video is None:
            raise VideoProxyProcessError(
                "downloaded source is not a playable video", phase="source_probe"
            )

    async def _probe_chunks(self, directory: Path) -> tuple[PreparedVideoChunk, ...]:
        paths = sorted(directory.glob("chunk_*.mp4"))
        if not paths:
            raise VideoProxyProcessError(
                "FFmpeg produced no proxy chunks", phase="chunk_probe"
            )
        chunks: list[PreparedVideoChunk] = []
        start_ms = 0
        for index, path in enumerate(paths):
            size_bytes = path.stat().st_size
            if size_bytes <= 0:
                raise VideoProxyProcessError(
                    "FFmpeg produced an empty proxy chunk", phase="chunk_probe"
                )
            if size_bytes >= int(self._settings.VIDEO_PROXY_MAX_CHUNK_BYTES):
                raise VideoProxyChunkTooLargeError("video proxy chunk exceeds configured maximum size")
            duration_ms, width, height = await self._ffprobe(path, phase="chunk_probe")
            end_ms = start_ms + duration_ms
            chunks.append(PreparedVideoChunk(index, path, start_ms, end_ms, duration_ms, size_bytes, width, height))
            start_ms = end_ms
        return tuple(chunks)

    async def _ffprobe(
        self, path: Path, *, phase: str = "chunk_probe"
    ) -> tuple[int, int | None, int | None]:
        document = await self._ffprobe_document(path, phase=phase)
        try:
            duration_ms = round(float(document["format"]["duration"]) * 1000)
        except (KeyError, TypeError, ValueError) as exc:
            raise VideoProxyProcessError(
                "ffprobe returned malformed proxy metadata", phase=phase
            ) from exc
        if not math.isfinite(duration_ms / 1000) or duration_ms <= 0:
            raise VideoProxyProcessError(
                "ffprobe returned a non-positive proxy duration", phase=phase
            )
        video = next(
            (
                stream
                for stream in document.get("streams", [])
                if isinstance(stream, dict) and stream.get("codec_type") == "video"
            ),
            None,
        )
        if video is None:
            raise VideoProxyProcessError("ffprobe found no proxy video stream", phase=phase)
        width = video.get("width")
        height = video.get("height")
        if width is not None and (not isinstance(width, int) or width <= 0):
            raise VideoProxyProcessError("ffprobe returned an invalid video width", phase=phase)
        if height is not None and (not isinstance(height, int) or height <= 0):
            raise VideoProxyProcessError("ffprobe returned an invalid video height", phase=phase)
        return duration_ms, width, height

    async def _ffprobe_document(self, path: Path, *, phase: str) -> dict[str, Any]:
        try:
            process = await self._create_subprocess_exec(
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,width,height",
                "-of", "json", str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise VideoProxyConfigurationError("ffprobe executable is unavailable") from exc
        stdout, _stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(
                "video_proxy_process_failed",
                extra={"phase": phase, "returncode": process.returncode},
            )
            raise VideoProxyProcessError(
                "ffprobe could not inspect video media",
                phase=phase,
                returncode=process.returncode,
            )
        try:
            document = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise VideoProxyProcessError(
                "ffprobe returned malformed metadata", phase=phase
            ) from exc
        if not isinstance(document, dict):
            raise VideoProxyProcessError("ffprobe returned malformed metadata", phase=phase)
        return document

    @staticmethod
    async def _close_stdin(process: Any) -> None:
        stdin = getattr(process, "stdin", None)
        if stdin is not None and not stdin.is_closing():
            stdin.close()
            waiter = getattr(stdin, "wait_closed", None)
            if waiter is not None:
                try:
                    await waiter()
                except (BrokenPipeError, ConnectionResetError):
                    pass

    @classmethod
    async def _terminate_process(cls, process: Any) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=cls._TERMINATE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    async def _cancel_task(task: asyncio.Task[Any]) -> None:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _remove_working_directory(directory: Path) -> None:
        shutil.rmtree(directory, ignore_errors=True)

    def cleanup_stale(self, *, dry_run: bool = False) -> tuple[int, int]:
        """Remove abandoned proxy workspaces older than the configured TTL."""
        root = self._configured_root().resolve()
        cutoff = time.time() - (getattr(self._settings, "VIDEO_PROXY_STALE_RETENTION_HOURS", 24) * 3600)
        removed = bytes_freed = 0
        for directory in root.glob("video-proxy-*"):
            if not directory.is_dir() or directory.is_symlink() or directory.stat().st_mtime >= cutoff:
                continue
            size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
            if not dry_run:
                shutil.rmtree(directory, ignore_errors=True)
            removed += 1
            bytes_freed += size
        return removed, bytes_freed

    def cleanup(self, chunks: tuple[PreparedVideoChunk, ...] | list[PreparedVideoChunk]) -> None:
        """Remove only proxy directories owned by this service."""
        root = self._configured_root()
        for directory in {chunk.path.parent.resolve() for chunk in chunks}:
            try:
                directory.relative_to(root)
            except ValueError:
                continue
            if directory.name.startswith("video-proxy-"):
                shutil.rmtree(directory, ignore_errors=True)

    @staticmethod
    def cleanup_prepared_chunks(chunks: tuple[PreparedVideoChunk, ...] | list[PreparedVideoChunk]) -> None:
        """Legacy compatibility cleanup for callers that still own prepared chunks."""
        for path in (chunk.path for chunk in chunks):
            path.unlink(missing_ok=True)
        for parent in {chunk.path.parent for chunk in chunks}:
            for source in ("source.mp4", "source.mov", "source.bin"):
                (parent / source).unlink(missing_ok=True)
            try:
                parent.rmdir()
            except OSError:
                pass
