from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.assets.content_resolver import SourceAssetContentResolver
from app.modules.assets.model import SourceAssetModel
from app.modules.pipeline.mime_types import is_eligible_video_source_asset
from app.modules.video_search.fingerprint import build_video_source_fingerprint


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
    """Transcode one provider stream into independently playable local chunks.

    The original is deliberately never materialized: provider blocks are copied to
    FFmpeg's stdin with ``drain`` backpressure and only derived MP4 chunks exist on
    the configured transient filesystem.
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
        if source_asset is None or not is_eligible_video_source_asset(source_asset):
            raise VideoProxySourceChangedError("source asset is unavailable or not a supported video")
        if build_video_source_fingerprint(source_asset) != expected_source_fingerprint:
            raise VideoProxySourceChangedError("source asset fingerprint changed before proxy preparation")

        root = self._configured_root()
        runtime_reserve = self._storage_safety_reserve()
        preflight_required = self._preflight_required_free_space(runtime_reserve)
        self._ensure_free_space(root, preflight_required)
        working_directory = Path(tempfile.mkdtemp(prefix="video-proxy-", dir=root))
        process: Any | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        storage_task: asyncio.Task[None] | None = None
        stop_monitor = asyncio.Event()
        storage_failed = asyncio.Event()
        try:
            command = self._ffmpeg_command(working_directory)
            try:
                process = await self._create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise VideoProxyConfigurationError("FFmpeg executable is unavailable") from exc
            if process.stdin is None or process.stderr is None:
                raise VideoProxyProcessError("FFmpeg did not expose stdin and stderr pipes")
            stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
            storage_task = asyncio.create_task(
                self._monitor_storage(process, root, runtime_reserve, stop_monitor, storage_failed)
            )
            try:
                async with self._resolver.open(
                    tenant_id=tenant_id, source_asset_id=source_asset_id, range_header=None
                ) as stream:
                    async for block in stream.body:
                        if storage_failed.is_set():
                            raise VideoProxyStorageError("insufficient free space while creating video proxy")
                        if not isinstance(block, bytes):
                            raise VideoProxyProcessError("provider stream emitted a non-bytes block")
                        process.stdin.write(block)
                        await process.stdin.drain()
            except asyncio.CancelledError:
                await self._close_stdin(process)
                await self._terminate_process(process)
                raise
            except Exception:
                await self._close_stdin(process)
                await self._terminate_process(process)
                raise
            await self._close_stdin(process)
            await process.wait()
            if storage_failed.is_set():
                raise VideoProxyStorageError("insufficient free space while creating video proxy")
            stderr_tail = await stderr_task
            stderr_task = None
            if process.returncode != 0:
                detail = stderr_tail.decode("utf-8", errors="replace").strip()
                raise VideoProxyProcessError(f"FFmpeg exited with status {process.returncode}: {detail}")
            chunks = await self._probe_chunks(working_directory)
            if self._load_fingerprint(tenant_id, source_asset_id) != expected_source_fingerprint:
                raise VideoProxySourceChangedError("source asset fingerprint changed during proxy preparation")
            return chunks
        except asyncio.CancelledError:
            if process is not None:
                await self._close_stdin(process)
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

    def _storage_safety_reserve(self) -> int:
        maximum = int(self._settings.VIDEO_PROXY_MAX_CHUNK_BYTES)
        if maximum <= 0:
            raise VideoProxyConfigurationError("VIDEO_PROXY_MAX_CHUNK_BYTES must be positive")
        return min(maximum, self._WORKING_RESERVE_BYTES)

    def _preflight_required_free_space(self, runtime_reserve: int | None = None) -> int:
        maximum = int(self._settings.VIDEO_PROXY_MAX_CHUNK_BYTES)
        reserve = self._storage_safety_reserve() if runtime_reserve is None else runtime_reserve
        return maximum + reserve

    def _ensure_free_space(self, root: Path, required: int) -> None:
        try:
            free = int(self._disk_usage(root).free)
        except OSError as exc:
            raise VideoProxyStorageError("cannot inspect free proxy storage") from exc
        if free < required:
            raise VideoProxyStorageError("insufficient free space for video proxy preparation")

    def _load_source_asset(self, tenant_id: str, source_asset_id: str) -> SourceAssetModel | None:
        with self._session_factory() as session:
            return session.scalar(select(SourceAssetModel).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.id == source_asset_id,
                SourceAssetModel.deleted_at.is_(None),
            ))

    def _load_fingerprint(self, tenant_id: str, source_asset_id: str) -> str | None:
        asset = self._load_source_asset(tenant_id, source_asset_id)
        return build_video_source_fingerprint(asset) if asset is not None else None

    def _ffmpeg_command(self, directory: Path) -> tuple[str, ...]:
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
        scale = f"scale=w='min(iw,{max_width})':h='min(ih,{max_height})':force_original_aspect_ratio=decrease:force_divisible_by=2"
        force_keyframes = f"expr:gte(t,n_forced*{chunk_seconds})"
        return (
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", "pipe:0",
            "-map", "0:v:0", "-map", "0:a?", "-vf", scale, "-r", str(fps),
            "-c:v", "libx264", "-b:v", f"{video_bitrate}k", "-c:a", "aac",
            "-b:a", f"{audio_bitrate}k", "-force_key_frames", force_keyframes,
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

    async def _probe_chunks(self, directory: Path) -> tuple[PreparedVideoChunk, ...]:
        paths = sorted(directory.glob("chunk_*.mp4"))
        if not paths:
            raise VideoProxyProcessError("FFmpeg produced no proxy chunks")
        chunks: list[PreparedVideoChunk] = []
        start_ms = 0
        for index, path in enumerate(paths):
            size_bytes = path.stat().st_size
            if size_bytes <= 0:
                raise VideoProxyProcessError("FFmpeg produced an empty proxy chunk")
            if size_bytes >= int(self._settings.VIDEO_PROXY_MAX_CHUNK_BYTES):
                raise VideoProxyChunkTooLargeError("video proxy chunk exceeds configured maximum size")
            duration_ms, width, height = await self._ffprobe(path)
            end_ms = start_ms + duration_ms
            chunks.append(PreparedVideoChunk(index, path, start_ms, end_ms, duration_ms, size_bytes, width, height))
            start_ms = end_ms
        return tuple(chunks)

    async def _ffprobe(self, path: Path) -> tuple[int, int | None, int | None]:
        try:
            process = await self._create_subprocess_exec(
                "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise VideoProxyConfigurationError("ffprobe executable is unavailable") from exc
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise VideoProxyProcessError(f"ffprobe failed: {stderr[-self._STDERR_TAIL_BYTES:].decode('utf-8', errors='replace')}")
        try:
            document = json.loads(stdout)
            duration_ms = round(float(document["format"]["duration"]) * 1000)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VideoProxyProcessError("ffprobe returned malformed proxy metadata") from exc
        if duration_ms <= 0:
            raise VideoProxyProcessError("ffprobe returned a non-positive proxy duration")
        video = next((stream for stream in document.get("streams", []) if stream.get("codec_type") == "video"), None)
        width = video.get("width") if isinstance(video, dict) else None
        height = video.get("height") if isinstance(video, dict) else None
        if width is not None and (not isinstance(width, int) or width <= 0):
            raise VideoProxyProcessError("ffprobe returned an invalid video width")
        if height is not None and (not isinstance(height, int) or height <= 0):
            raise VideoProxyProcessError("ffprobe returned an invalid video height")
        return duration_ms, width, height

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
        """Legacy compatibility cleanup without recursive deletion."""
        for path in (chunk.path for chunk in chunks):
            path.unlink(missing_ok=True)
        for parent in {chunk.path.parent for chunk in chunks}:
            try:
                parent.rmdir()
            except OSError:
                pass
