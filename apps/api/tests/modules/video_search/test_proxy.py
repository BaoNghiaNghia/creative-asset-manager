import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.providers.contracts import AssetDownloadStream
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.video_search.fingerprint import build_video_source_fingerprint
from app.modules.video_search.proxy import (
    VideoProxyChunkTooLargeError,
    VideoProxyConfigurationError,
    VideoProxyPreparationService,
    VideoProxySourceChangedError,
    VideoProxySourceError,
    VideoProxyStorageError,
    VideoProxyProcessError,
)


class FakeStdin:
    def __init__(self):
        self.writes, self.drains, self.closed = [], 0, False
    def write(self, block): self.writes.append(block)
    async def drain(self): self.drains += 1
    def close(self): self.closed = True
    def is_closing(self): return self.closed
    async def wait_closed(self): pass


class FakeReader:
    async def read(self, _size): return b""


class FakeProcess:
    def __init__(self, output_path=None, *, probe=False, duration=1.25):
        self.stdin = None if probe else FakeStdin()
        self.stderr, self.returncode = FakeReader(), None
        self.output_path, self.probe, self.duration = output_path, probe, duration
        self.terminated, self.killed = False, False
    async def wait(self):
        if not self.probe and self.output_path is not None and not self.terminated:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_bytes(b"proxy")
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode
    async def communicate(self):
        self.returncode = 0
        return json.dumps({"format":{"duration":str(self.duration)},"streams":[{"codec_type":"video","width":640,"height":360}]}).encode(), b""
    def terminate(self): self.terminated = True; self.returncode = -15
    def kill(self): self.killed = True; self.returncode = -9


class FakeProcessFactory:
    def __init__(self): self.calls, self.ffmpeg = [], None
    async def __call__(self, *command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == "ffprobe": return FakeProcess(probe=True)
        self.ffmpeg = FakeProcess(Path(command[-1].replace("%05d", "00000")))
        return self.ffmpeg


class FakeResolver:
    def __init__(self, blocks=(b"a" * 20,), error=None):
        self.blocks, self.error, self.open_calls = blocks, error, []
    @asynccontextmanager
    async def open(self, **kwargs):
        self.open_calls.append(kwargs)
        async def body():
            for block in self.blocks: yield block
            if self.error: raise self.error
        async def close(): pass
        yield AssetDownloadStream(body=body(), close=close)


class VideoProxyPreparationServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add(ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_type="google_drive", source_key="drive-a", source_metadata={}))
            session.add(SourceAssetModel(id="asset-a", tenant_id="tenant-a", external_source_id="source-a", external_asset_id="file-a", filename="clip.mp4", mime_type="video/mp4", size_bytes=20, provider_checksum="checksum", provider_version="v1", source_metadata={}))
            session.commit()
    def tearDown(self):
        self.engine.dispose(); self.temp.cleanup()
    def settings(self, **overrides):
        values = dict(VIDEO_TEMP_DIRECTORY=self.temp.name, VIDEO_PROXY_MAX_WIDTH=1280, VIDEO_PROXY_MAX_HEIGHT=720, VIDEO_PROXY_FPS=15, VIDEO_PROXY_VIDEO_BITRATE_KBPS=1500, VIDEO_PROXY_AUDIO_BITRATE_KBPS=64, VIDEO_CHUNK_SECONDS=2, VIDEO_PROXY_MAX_CHUNK_BYTES=1000)
        values.update(overrides); return SimpleNamespace(**values)
    def fingerprint(self):
        with self.sessions() as session: return build_video_source_fingerprint(session.get(SourceAssetModel, "asset-a"))
    def service(self, factory=None, resolver=None, settings=None, free=10**9):
        return VideoProxyPreparationService(self.sessions, settings or self.settings(), content_resolver=resolver or FakeResolver(), create_subprocess_exec=factory or FakeProcessFactory(), disk_usage=lambda _path: SimpleNamespace(free=free), storage_poll_seconds=.001)
    def test_streams_many_blocks_to_seekable_source_file_and_builds_safe_command(self):
        factory, resolver = FakeProcessFactory(), FakeResolver(tuple(bytes([n]) for n in range(20)))
        service = self.service(factory, resolver)
        chunks = asyncio.run(service.prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        command = factory.calls[0][0]
        self.assertIs(factory.calls[0][1]["stdin"], asyncio.subprocess.DEVNULL)
        self.assertEqual(resolver.open_calls[0]["range_header"], None)
        self.assertNotIn("pipe:0", command); self.assertIn("libx264", command); self.assertIn("aac", command)
        source_path = Path(command[command.index("-i") + 1])
        self.assertEqual(source_path.read_bytes(), bytes(range(20)))
        self.assertIn("0:a?", command); self.assertIn("-segment_time", command); self.assertNotIn("Authorization", " ".join(command))
        self.assertEqual(chunks[0].source_start_ms, 0); self.assertEqual(chunks[0].source_end_ms, 1250); self.assertTrue(chunks[0].path.exists())
        service.cleanup(chunks); self.assertFalse(chunks[0].path.exists()); self.assertFalse(chunks[0].path.parent.exists())
    def test_deleted_source_reaches_resolver_for_move_recovery(self):
        with self.sessions() as session:
            asset = session.get(SourceAssetModel, "asset-a")
            asset.deleted_at = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            )
            session.commit()
        resolver = FakeResolver()
        service = self.service(resolver=resolver)
        chunks = asyncio.run(service.prepare(
            tenant_id="tenant-a",
            source_asset_id="asset-a",
            expected_source_fingerprint=self.fingerprint(),
        ))
        self.assertEqual(len(resolver.open_calls), 1)
        service.cleanup(chunks)

    def test_quicktime_uses_a_private_mov_source_path(self):
        with self.sessions() as session:
            asset = session.get(SourceAssetModel, "asset-a")
            asset.mime_type = "video/quicktime"
            asset.filename = "clip.mov"
            session.commit()
        factory = FakeProcessFactory()
        chunks = asyncio.run(self.service(factory).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        command = factory.calls[0][0]
        self.assertTrue(command[command.index("-i") + 1].endswith("source.mov"))
        self.assertNotIn("pipe:0", command)
        self.service().cleanup(chunks)

    def test_multiple_chunks_have_independent_files_and_contiguous_timeline(self):
        factory = FakeProcessFactory()
        original = factory.__call__
        async def multiple_chunks(*command, **kwargs):
            process = await original(*command, **kwargs)
            if command[0] == "ffmpeg":
                original_wait = process.wait
                async def wait():
                    result = await original_wait()
                    Path(str(process.output_path).replace("00000", "00001")).write_bytes(b"proxy-two")
                    return result
                process.wait = wait
            return process
        service = self.service(multiple_chunks)
        chunks = asyncio.run(service.prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertEqual([chunk.chunk_index for chunk in chunks], [0, 1])
        self.assertTrue(all(chunk.path.exists() and chunk.size_bytes > 0 for chunk in chunks))
        self.assertEqual(chunks[0].source_end_ms, chunks[1].source_start_ms)
        service.cleanup(chunks)

    def test_command_uses_input_aware_scale_and_segment_mp4_options(self):
        service = self.service()
        command = service._ffmpeg_command(Path(self.temp.name))
        scale = command[command.index("-vf") + 1]
        self.assertIn("min(iw,1280)", scale)
        self.assertIn("min(ih,720)", scale)
        self.assertIn("-segment_format", command)
        self.assertEqual(command[command.index("-segment_format") + 1], "mp4")
        self.assertEqual(command[command.index("-segment_format_options") + 1], "movflags=+faststart")
        self.assertNotIn("-movflags", command)

    def test_storage_preflight_and_runtime_thresholds_are_separate(self):
        service = self.service(settings=self.settings(VIDEO_PROXY_MAX_CHUNK_BYTES=1000))
        self.assertEqual(service._storage_safety_reserve(), 1000)
        self.assertEqual(service._output_storage_requirement(), 2000)
        self.assertEqual(service._preflight_required_free_space(20), 2020)
        service._ensure_free_space(Path(self.temp.name), 1000)

    def test_configuration_and_storage_preflight_fail_before_ffmpeg(self):
        factory = FakeProcessFactory()
        with self.assertRaises(VideoProxyConfigurationError):
            asyncio.run(self.service(factory, settings=self.settings(VIDEO_TEMP_DIRECTORY="")).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        with self.assertRaises(VideoProxyStorageError):
            asyncio.run(self.service(factory, free=1).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertEqual(factory.calls, [])
    def test_initial_fingerprint_mismatch_does_not_start_ffmpeg(self):
        factory = FakeProcessFactory()
        with self.assertRaises(VideoProxySourceChangedError):
            asyncio.run(self.service(factory).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint="wrong"))
        self.assertEqual(factory.calls, [])
    def test_source_failure_terminates_process_and_removes_partial_files(self):
        factory = FakeProcessFactory()
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            asyncio.run(self.service(factory, FakeResolver(error=RuntimeError("provider failed"))).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertIsNone(factory.ffmpeg); self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])
    def test_oversize_chunk_is_rejected_and_cleaned(self):
        factory = FakeProcessFactory()
        with self.assertRaises(VideoProxyChunkTooLargeError):
            asyncio.run(self.service(factory, settings=self.settings(VIDEO_PROXY_MAX_CHUNK_BYTES=3, VIDEO_PROXY_MAX_SOURCE_BYTES=100), free=10**9).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])
    def test_exact_chunk_size_limit_is_rejected_and_cleaned(self):
        factory = FakeProcessFactory()
        with self.assertRaises(VideoProxyChunkTooLargeError):
            asyncio.run(self.service(factory, settings=self.settings(VIDEO_PROXY_MAX_CHUNK_BYTES=5, VIDEO_PROXY_MAX_SOURCE_BYTES=100), free=10**9).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])
    def test_truncated_source_is_rejected_and_cleaned(self):
        factory = FakeProcessFactory()
        with self.assertRaises(VideoProxySourceError):
            asyncio.run(self.service(factory, FakeResolver((b"short",))).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertIsNone(factory.ffmpeg)
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])

    def test_source_limit_is_enforced_and_cleaned(self):
        factory = FakeProcessFactory()
        with self.assertRaises(VideoProxySourceError):
            asyncio.run(self.service(factory, FakeResolver((b"a" * 21,)), settings=self.settings(VIDEO_PROXY_MAX_SOURCE_BYTES=20)).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertIsNone(factory.ffmpeg)
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])

    def test_process_failure_is_reported_and_cleaned(self):
        factory = FakeProcessFactory()
        original = factory.__call__
        async def failing(*command, **kwargs):
            process = await original(*command, **kwargs)
            if command[0] == "ffmpeg": process.returncode = 7
            return process
        with self.assertRaises(VideoProxyProcessError):
            asyncio.run(self.service(failing).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])

    def test_final_fingerprint_change_removes_all_proxies(self):
        factory = FakeProcessFactory()
        original = factory.__call__
        async def changes_source(*command, **kwargs):
            process = await original(*command, **kwargs)
            if command[0] == "ffmpeg":
                original_wait = process.wait
                async def wait():
                    result = await original_wait()
                    with self.sessions() as session:
                        session.get(SourceAssetModel, "asset-a").provider_version = "v2"
                        session.commit()
                    return result
                process.wait = wait
            return process
        with self.assertRaises(VideoProxySourceChangedError):
            asyncio.run(self.service(changes_source).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])

    def test_storage_monitor_terminates_process_when_space_falls_below_reserve(self):
        process = FakeProcess()
        service = self.service(free=1)
        async def monitor():
            failed, stop = asyncio.Event(), asyncio.Event()
            await service._monitor_storage(process, Path(self.temp.name), 2, stop, failed)
            return failed.is_set()
        self.assertTrue(asyncio.run(monitor()))
        self.assertTrue(process.terminated)

    def test_runtime_low_space_during_prepare_terminates_and_cleans_up(self):
        factory = FakeProcessFactory()
        calls = 0
        def disk_usage(_path):
            nonlocal calls
            calls += 1
            return SimpleNamespace(free=10**9 if calls < 3 else 1)
        class DelayedResolver(FakeResolver):
            @asynccontextmanager
            async def open(self, **kwargs):
                async def body():
                    yield b"first"
                    await asyncio.sleep(.02)
                    yield b"second"
                async def close(): pass
                yield AssetDownloadStream(body=body(), close=close)
        service = VideoProxyPreparationService(self.sessions, self.settings(), content_resolver=DelayedResolver(), create_subprocess_exec=factory, disk_usage=disk_usage, storage_poll_seconds=.001)
        with self.assertRaises(VideoProxyStorageError):
            asyncio.run(service.prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertIsNone(factory.ffmpeg)
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])

    def test_cancellation_before_ffmpeg_creation_cleans_working_directory(self):
        entered = asyncio.Event()
        class SlowResolver(FakeResolver):
            @asynccontextmanager
            async def open(self, **kwargs):
                self.open_calls.append(kwargs)
                async def body():
                    entered.set()
                    yield b"first"
                    await asyncio.Event().wait()
                async def close(): pass
                yield AssetDownloadStream(body=body(), close=close)
        factory = FakeProcessFactory()
        async def cancel():
            task = asyncio.create_task(self.service(factory, SlowResolver()).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        asyncio.run(cancel())
        self.assertIsNone(factory.ffmpeg)
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])


    def test_missing_ffmpeg_is_configuration_error_and_cleans_work_directory(self):
        async def factory(*command, **_kwargs):
            if command[0] == "ffmpeg":
                raise FileNotFoundError("missing")
        before = set(Path(self.temp.name).glob("video-proxy-*"))
        with self.assertRaises(VideoProxyConfigurationError):
            asyncio.run(self.service(factory).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertEqual(set(Path(self.temp.name).glob("video-proxy-*")), before)

    def test_missing_ffprobe_is_configuration_error_and_cleans_work_directory(self):
        factory = FakeProcessFactory()
        original = factory.__call__
        async def missing_probe(*command, **kwargs):
            if command[0] == "ffprobe":
                raise FileNotFoundError("missing")
            return await original(*command, **kwargs)
        with self.assertRaises(VideoProxyConfigurationError):
            asyncio.run(self.service(missing_probe).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self.fingerprint()))
        self.assertEqual(list(Path(self.temp.name).glob("video-proxy-*")), [])


if __name__ == "__main__":
    unittest.main()
