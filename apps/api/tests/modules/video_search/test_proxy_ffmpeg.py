import asyncio
import json
import shutil
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
from app.modules.video_search.proxy import VideoProxyPreparationService


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
class VideoProxyRealFfmpegTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add(ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_type="google_drive", source_key="drive-a", source_metadata={}))
            session.add(SourceAssetModel(id="asset-a", tenant_id="tenant-a", external_source_id="source-a", external_asset_id="file-a", filename="source.mp4", mime_type="video/mp4", size_bytes=1, provider_checksum="checksum", provider_version="v1", source_metadata={}))
            session.commit()

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    async def _run(self, *command):
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise AssertionError(stderr.decode())
        return stdout

    def _settings(self, chunk_seconds=20):
        return SimpleNamespace(VIDEO_TEMP_DIRECTORY=str(self.root / "proxies"), VIDEO_PROXY_MAX_WIDTH=1280, VIDEO_PROXY_MAX_HEIGHT=720, VIDEO_PROXY_FPS=15, VIDEO_PROXY_VIDEO_BITRATE_KBPS=1500, VIDEO_PROXY_AUDIO_BITRATE_KBPS=64, VIDEO_CHUNK_SECONDS=chunk_seconds, VIDEO_PROXY_MAX_CHUNK_BYTES=100_000_000)

    async def _fixture(self, width, height, duration):
        path = self.root / f"source-{width}x{height}-{duration}.mp4"
        await self._run("ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30", "-t", str(duration), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path))
        return path

    def _fingerprint(self):
        with self.sessions() as session:
            return build_video_source_fingerprint(session.get(SourceAssetModel, "asset-a"))

    def _service(self, fixture, *, chunk_seconds=20):
        @asynccontextmanager
        async def open_stream(**kwargs):
            self.assertIsNone(kwargs["range_header"])
            async def body():
                with fixture.open("rb") as source:
                    while block := source.read(4096):
                        yield block
            async def close(): pass
            yield AssetDownloadStream(body=body(), close=close)
        return VideoProxyPreparationService(self.sessions, self._settings(chunk_seconds), content_resolver=SimpleNamespace(open=open_stream))

    async def _probe(self, path):
        return json.loads(await self._run("ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type,width,height,avg_frame_rate", "-of", "json", str(path)))

    def _prepare(self, width, height, duration, *, chunk_seconds=20):
        async def scenario():
            fixture = await self._fixture(width, height, duration)
            chunks = await self._service(fixture, chunk_seconds=chunk_seconds).prepare(tenant_id="tenant-a", source_asset_id="asset-a", expected_source_fingerprint=self._fingerprint())
            return chunks, [await self._probe(chunk.path) for chunk in chunks]
        return asyncio.run(scenario())

    @staticmethod
    def _video(document):
        return next(stream for stream in document["streams"] if stream["codec_type"] == "video")

    def test_no_upscale_h264_fps_and_no_audio(self):
        chunks, probes = self._prepare(640, 360, 1)
        self.assertEqual(len(chunks), 1)
        video = self._video(probes[0])
        self.assertEqual((video["width"], video["height"]), (640, 360))
        self.assertEqual(video["codec_name"], "h264")
        numerator, denominator = (int(value) for value in video["avg_frame_rate"].split("/"))
        self.assertAlmostEqual(numerator / denominator, 15, delta=.5)
        self.assertEqual([stream["codec_type"] for stream in probes[0]["streams"]].count("audio"), 0)

    def test_downscale_and_four_three_aspect_ratio(self):
        chunks, probes = self._prepare(1920, 1080, 1)
        video = self._video(probes[0])
        self.assertEqual((video["width"], video["height"]), (1280, 720))
        chunks, probes = self._prepare(640, 480, 1)
        video = self._video(probes[0])
        self.assertEqual((video["width"], video["height"]), (640, 480))

    def test_multiple_independent_chunks_have_contiguous_timeline(self):
        chunks, probes = self._prepare(640, 360, 5, chunk_seconds=2)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual([chunk.chunk_index for chunk in chunks], list(range(len(chunks))))
        for chunk, probe in zip(chunks, probes):
            self.assertTrue(chunk.path.exists())
            self.assertGreater(chunk.size_bytes, 0)
            self.assertGreater(chunk.duration_ms, 0)
            self.assertEqual(self._video(probe)["codec_name"], "h264")
        for previous, current in zip(chunks, chunks[1:]):
            self.assertEqual(previous.source_end_ms, current.source_start_ms)


if __name__ == "__main__":
    unittest.main()
