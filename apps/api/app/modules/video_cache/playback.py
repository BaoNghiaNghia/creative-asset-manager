"""Best-effort derived MP4 playback preparation for Public Review."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_cache.model import VideoCacheObjectModel, utcnow
from app.modules.video_cache.quota import VideoCacheQuota
from app.modules.video_cache.service import video_cache_key, video_playback_key
from app.providers.cloudflare.r2 import R2Adapter, R2ProviderError


class VideoPlaybackPreparationError(RuntimeError):
    pass


class VideoPlaybackConfigurationError(VideoPlaybackPreparationError):
    pass


class VideoPlaybackDiskSpaceError(VideoPlaybackPreparationError):
    pass


class VideoPlaybackTransientError(VideoPlaybackPreparationError):
    pass


class VideoPlaybackProcessError(VideoPlaybackPreparationError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPlayback:
    key: str
    kind: str
    size_bytes: int
    etag: str | None


def schedule_video_playback_prepare(
    session: Session,
    settings: Settings,
    row: VideoCacheObjectModel,
) -> bool:
    """Reserve bounded bucket bytes and enqueue one idempotent derivative job."""
    if (
        not settings.R2_VIDEO_PLAYBACK_DERIVED_ENABLED
        or not settings.PROCESSING_JOBS_ENABLED
    ):
        return False

    # Serialize against original-fill admission and other derivative admission.
    # PostgreSQL holds this advisory transaction lock through the caller commit.
    with VideoCacheQuota.reservation_lock(session):
        session.refresh(row)
        if row.status != "ready":
            return False
        if row.playback_status in {"ready", "preparing", "failed", "skipped"}:
            return False
        if row.size_bytes <= 0 or row.size_bytes > settings.R2_VIDEO_PLAYBACK_MAX_SOURCE_BYTES:
            row.playback_status = "skipped"
            row.playback_error_code = "source_too_large"
            row.playback_reserved_bytes = 0
            row.playback_updated_at = utcnow()
            session.flush()
            return True

        reserve = min(row.size_bytes, settings.R2_VIDEO_PLAYBACK_MAX_OUTPUT_BYTES)
        usage = VideoCacheQuota.usage(session)
        if usage.effective_bytes + reserve > settings.R2_VIDEO_CACHE_HARD_LIMIT_BYTES:
            row.playback_status = "skipped"
            row.playback_error_code = "quota"
            row.playback_reserved_bytes = 0
            row.playback_updated_at = utcnow()
            session.flush()
            return True

        generation = row.playback_generation + 1
        job = ProcessingRepository(session, settings).create_job(
            tenant_id=row.tenant_id,
            job_type="video_playback_prepare",
            entity_type="video_cache_object",
            entity_id=row.id,
            idempotency_key=f"video-playback:{row.content_hash}:{generation}",
            payload={
                "tenant_id": row.tenant_id,
                "asset_id": row.asset_id,
                "source_asset_id": row.source_asset_id,
                "content_hash": row.content_hash,
                "generation": generation,
            },
            max_attempts=3,
        )
        row.playback_r2_key = video_playback_key(row.tenant_id, row.content_hash)
        row.playback_status = "preparing"
        row.playback_reserved_bytes = reserve
        row.playback_job_id = job.id
        row.playback_generation = generation
        row.playback_error_code = None
        row.playback_updated_at = utcnow()
        session.flush()
        return True


class VideoPlaybackPreparationService:
    _WORKING_RESERVE_BYTES = 128 * 1024 * 1024
    _STDERR_LIMIT = 16 * 1024

    def __init__(
        self,
        settings: Settings,
        provider: R2Adapter,
        *,
        create_subprocess_exec: Callable[..., Any] = asyncio.create_subprocess_exec,
        disk_usage: Callable[[str | Path], Any] = shutil.disk_usage,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self._create_subprocess_exec = create_subprocess_exec
        self._disk_usage = disk_usage

    def _temp_root(self) -> Path | None:
        value = self.settings.VIDEO_TEMP_DIRECTORY.strip()
        if not value:
            return None
        root = Path(value)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _ensure_space(self, root: Path, source_bytes: int, output_reserve: int) -> None:
        required = source_bytes + output_reserve + self._WORKING_RESERVE_BYTES
        try:
            free = int(self._disk_usage(root).free)
        except OSError as exc:
            raise VideoPlaybackDiskSpaceError("cannot inspect playback temp storage") from exc
        if free < required:
            raise VideoPlaybackDiskSpaceError("insufficient playback temp storage")

    async def _probe(self, source: Path) -> dict[str, Any]:
        try:
            process = await self._create_subprocess_exec(
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=format_name,bit_rate,duration:stream=codec_type,codec_name,width,height",
                "-of", "json",
                str(source),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise VideoPlaybackConfigurationError("ffprobe is unavailable") from exc
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise VideoPlaybackProcessError("ffprobe failed")
        try:
            value = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VideoPlaybackProcessError("ffprobe returned invalid metadata") from exc
        if not isinstance(value, dict):
            raise VideoPlaybackProcessError("ffprobe returned invalid metadata")
        return value

    def _kind(self, document: dict[str, Any]) -> str:
        streams = document.get("streams")
        if not isinstance(streams, list):
            raise VideoPlaybackProcessError("video stream metadata is unavailable")
        video = next(
            (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
            None,
        )
        audio = next(
            (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
            None,
        )
        if video is None:
            raise VideoPlaybackProcessError("video stream metadata is unavailable")
        try:
            width = int(video.get("width") or 0)
            height = int(video.get("height") or 0)
        except (TypeError, ValueError) as exc:
            raise VideoPlaybackProcessError("video dimensions are invalid") from exc
        fmt = document.get("format") if isinstance(document.get("format"), dict) else {}
        try:
            bitrate_kbps = int(fmt.get("bit_rate") or 0) // 1000
        except (TypeError, ValueError):
            bitrate_kbps = 0
        formats = {
            item.strip()
            for item in str(fmt.get("format_name") or "").split(",")
            if item.strip()
        }
        copy_safe = (
            bool(formats.intersection({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}))
            and video.get("codec_name") == "h264"
            and (audio is None or audio.get("codec_name") == "aac")
            and 0 < width <= self.settings.R2_VIDEO_PLAYBACK_MAX_WIDTH
            and 0 < height <= self.settings.R2_VIDEO_PLAYBACK_MAX_HEIGHT
            and 0 < bitrate_kbps <= self.settings.R2_VIDEO_PLAYBACK_PROXY_THRESHOLD_KBPS
        )
        return "faststart" if copy_safe else "proxy_1080p"

    def _command(self, source: Path, output: Path, kind: str) -> tuple[str, ...]:
        base = (
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-map_metadata", "-1", "-map_chapters", "-1",
        )
        if kind == "faststart":
            return (*base, "-c", "copy", "-movflags", "+faststart", str(output))
        width = self.settings.R2_VIDEO_PLAYBACK_MAX_WIDTH
        height = self.settings.R2_VIDEO_PLAYBACK_MAX_HEIGHT
        bitrate = self.settings.R2_VIDEO_PLAYBACK_VIDEO_BITRATE_KBPS
        audio = self.settings.R2_VIDEO_PLAYBACK_AUDIO_BITRATE_KBPS
        scale = (
            f"scale=w='min(iw,{width})':h='min(ih,{height})':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p"
        )
        return (
            *base,
            "-vf", scale,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k", "-bufsize", f"{bitrate * 2}k",
            "-c:a", "aac", "-b:a", f"{audio}k", "-ar", "48000", "-ac", "2",
            "-avoid_negative_ts", "make_zero", "-max_muxing_queue_size", "2048",
            "-movflags", "+faststart",
            str(output),
        )

    async def _run_ffmpeg(self, command: tuple[str, ...]) -> None:
        try:
            process = await self._create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise VideoPlaybackConfigurationError("ffmpeg is unavailable") from exc
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.settings.R2_VIDEO_PLAYBACK_PREPARATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise VideoPlaybackTransientError("playback preparation timed out") from exc
        if process.returncode != 0:
            tail = bytes(stderr or b"")[-self._STDERR_LIMIT:]
            del tail
            raise VideoPlaybackProcessError("ffmpeg could not prepare playback")

    async def prepare_and_upload(
        self,
        *,
        tenant_id: str,
        content_hash: str,
        source_size_bytes: int,
        output_reserve_bytes: int,
    ) -> PreparedPlayback:
        original_key = video_cache_key(tenant_id, content_hash)
        playback_key = video_playback_key(tenant_id, content_hash)
        root = self._temp_root()
        space_root = root or Path(tempfile.gettempdir())
        self._ensure_space(space_root, source_size_bytes, output_reserve_bytes)
        with tempfile.TemporaryDirectory(prefix="review-playback-", dir=str(root) if root else None) as directory:
            source = Path(directory) / "source"
            output = Path(directory) / "playback.mp4"
            source_head = await self.provider.download_file(original_key, str(source))
            if (
                source_head.size_bytes != source_size_bytes
                or not source.exists()
                or source.stat().st_size != source_size_bytes
            ):
                raise VideoPlaybackPreparationError("downloaded original size mismatch")
            document = await self._probe(source)
            kind = self._kind(document)
            await self._run_ffmpeg(self._command(source, output, kind))
            size = output.stat().st_size if output.exists() else 0
            if size <= 0 or size > output_reserve_bytes or size > self.settings.R2_VIDEO_PLAYBACK_MAX_OUTPUT_BYTES:
                raise VideoPlaybackDiskSpaceError("prepared playback exceeds reserved bytes")
            head = await self.provider.upload_file(playback_key, str(output), "video/mp4")
            if head.size_bytes != size:
                try:
                    await self.provider.delete_object(playback_key)
                finally:
                    raise VideoPlaybackPreparationError("uploaded playback size mismatch")
            return PreparedPlayback(playback_key, kind, size, head.etag)


class VideoPlaybackPrepareJobHandler:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _row(self, session: Session, context: JobHandlerContext) -> VideoCacheObjectModel | None:
        job = context.job
        return session.scalar(select(VideoCacheObjectModel).where(
            VideoCacheObjectModel.tenant_id == job.tenant_id,
            VideoCacheObjectModel.id == job.entity_id,
            VideoCacheObjectModel.status == "ready",
            VideoCacheObjectModel.playback_status == "preparing",
            VideoCacheObjectModel.playback_job_id == job.id,
        ))

    def _valid(self, row: VideoCacheObjectModel | None, context: JobHandlerContext) -> bool:
        if row is None:
            return False
        payload = context.job.payload
        return bool(
            payload.get("tenant_id") == context.job.tenant_id
            and payload.get("content_hash") == row.content_hash
            and payload.get("asset_id") == row.asset_id
            and payload.get("source_asset_id") == row.source_asset_id
            and payload.get("generation") == row.playback_generation
            and row.r2_key == video_cache_key(row.tenant_id, row.content_hash)
            and row.playback_reserved_bytes > 0
        )

    def _mark_failed(self, session_factory, context: JobHandlerContext, code: str) -> None:
        with session_factory() as session:
            row = self._row(session, context)
            if row is not None:
                row.playback_status = "failed"
                row.playback_reserved_bytes = 0
                row.playback_error_code = code
                row.playback_updated_at = utcnow()
                session.commit()

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        if not self.settings.R2_VIDEO_PLAYBACK_DERIVED_ENABLED:
            self._mark_failed(context.dependencies.session_factory, context, "disabled")
            return JobHandlerResult.non_retryable("playback_disabled", "Derived playback is disabled")
        if context.is_cancelled or context.shutdown_requested.is_set():
            return JobHandlerResult.cancelled()
        try:
            return asyncio.run(self._run(context))
        except Exception:
            self._mark_failed(context.dependencies.session_factory, context, "internal")
            return JobHandlerResult.non_retryable("playback_internal", "Derived playback preparation failed")

    async def _run(self, context: JobHandlerContext) -> JobHandlerResult:
        factory = context.dependencies.session_factory
        with factory() as session:
            row = self._row(session, context)
            if not self._valid(row, context):
                return JobHandlerResult.non_retryable("playback_state_changed", "Playback state changed")
            assert row is not None
            identity = (
                row.tenant_id,
                row.content_hash,
                row.size_bytes,
                row.playback_reserved_bytes,
                row.playback_generation,
            )

        provider = context.dependencies.resources.get("video_cache_r2_provider") or R2Adapter(self.settings)
        preparer = context.dependencies.resources.get("video_playback_preparer")
        if preparer is None:
            preparer = VideoPlaybackPreparationService(self.settings, provider)
        try:
            prepared = await preparer.prepare_and_upload(
                tenant_id=identity[0],
                content_hash=identity[1],
                source_size_bytes=identity[2],
                output_reserve_bytes=identity[3],
            )
            if context.is_cancelled or context.shutdown_requested.is_set():
                await provider.delete_object(prepared.key)
                return JobHandlerResult.cancelled()
            stale = False
            with factory() as session:
                row = self._row(session, context)
                stale = (
                    not self._valid(row, context)
                    or row is None
                    or row.playback_generation != identity[4]
                )
                if not stale and row is not None:
                    row.playback_r2_key = prepared.key
                    row.playback_kind = prepared.kind
                    row.playback_size_bytes = prepared.size_bytes
                    row.playback_reserved_bytes = 0
                    row.playback_etag = prepared.etag
                    row.playback_status = "ready"
                    row.playback_error_code = None
                    row.playback_updated_at = utcnow()
                    session.commit()
            if stale:
                await provider.delete_object(prepared.key)
                return JobHandlerResult.non_retryable("playback_state_changed", "Playback state changed")
            return JobHandlerResult.completed()
        except Exception as exc:
            retryable = (
                isinstance(exc, R2ProviderError) and exc.retryable
                or isinstance(exc, (VideoPlaybackDiskSpaceError, VideoPlaybackTransientError))
            )
            if retryable and context.job.attempt_count < 3:
                return JobHandlerResult.retryable("playback_transient", "Derived playback will retry")
            self._mark_failed(factory, context, "playback_prepare_failed")
            try:
                await provider.delete_object(video_playback_key(identity[0], identity[1]))
            except Exception:
                pass
            return JobHandlerResult.non_retryable("playback_prepare_failed", "Derived playback preparation failed")
