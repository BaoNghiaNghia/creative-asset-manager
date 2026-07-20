import io
import unittest

from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.providers.contracts import (
    AiMetadataAnalysisResult,
    StoredAssetReadStream,
)
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.service import AiAnalysisService
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.model import AssetStorageObjectModel


class FakeStorage:
    def __init__(self, content):
        self.content = content

    async def open_asset(self, _input):
        async def body():
            yield self.content
        async def close():
            return None
        return StoredAssetReadStream(body=body(), close=close, content_type="image/png")

    async def store_asset(self, _input):
        raise NotImplementedError

    async def store_metadata_sidecar(self, _input):
        raise NotImplementedError


class FakeAi:
    def __init__(self, metadata):
        self.metadata = metadata
        self.calls = 0

    async def analyze_single(self, _input):
        self.calls += 1
        return AiMetadataAnalysisResult(
            metadata=self.metadata,
            provider="fake",
            model="fake-1",
            provider_request_id="request-1",
            usage={"total": 1},
            provider_metadata={"finish_reason": "STOP"},
            raw_response={"safe": "audit"},
        )


class AiAnalysisServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(
            self.engine, class_=Session, expire_on_commit=False
        )
        image = io.BytesIO()
        Image.new("RGB", (20, 10), "red").save(image, format="PNG")
        self.storage = FakeStorage(image.getvalue())
        with self.factory() as session:
            asset = AssetModel(
                tenant_id="tenant-a", content_hash="a" * 64,
                mime_type="image/png", size_bytes=len(image.getvalue()),
            )
            session.add(asset)
            session.flush()
            profile = AiMetadataRepository(session).create_profile(
                tenant_id="tenant-a",
                profile_name="general",
                profile_version="1",
                prompt_template="Analyze {{ asset }}",
                optional_json_schema={
                    "type": "object",
                    "properties": {"subject": {"type": "string"}},
                    "required": ["subject"],
                },
                search_config={"facet_paths": {"subject": ["subject"]}},
            )
            analysis = AiMetadataRepository(session).create_analysis(
                tenant_id="tenant-a",
                asset_id=asset.id,
                metadata_profile_id=profile.id,
                prompt_version="profile-1",
                pipeline_version="single-asset-v1",
            )
            session.add(AssetStorageObjectModel(
                tenant_id="tenant-a",
                asset_id=asset.id,
                content_hash=asset.content_hash,
                storage_provider="google_drive_managed",
                status="stored",
                remote_file_id="remote-1",
                remote_folder_id="folder-1",
            ))
            session.commit()
            self.asset_id = asset.id
            self.analysis_id = analysis.id
        self.settings = Settings(
            DYNAMIC_AI_METADATA_ENABLED=True,
            AI_SINGLE_ANALYSIS_ENABLED=True,
            GEMINI_API_KEY="test-only",
        )

    def tearDown(self):
        self.engine.dispose()

    async def test_end_to_end_is_idempotent_and_enqueues_index(self):
        ai = FakeAi({"subject": "Cat", "nested": {"year": 2015}})
        service = AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=ai,
            settings=self.settings,
        )
        first = await service.analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )
        second = await service.analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-b",
        )
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertEqual(ai.calls, 1)
        with self.factory() as session:
            analysis = session.get(
                __import__(
                    "app.modules.ai_metadata.model",
                    fromlist=["AssetAiAnalysisModel"],
                ).AssetAiAnalysisModel,
                self.analysis_id,
            )
            asset = session.get(AssetModel, self.asset_id)
            self.assertEqual(analysis.status, "completed")
            self.assertIn("cat", analysis.search_projection["normalized_terms"])
            self.assertEqual(analysis.provider_request_id, "request-1")
            self.assertIsNone(analysis.raw_response_json)
            self.assertEqual(len(asset.analysis_image_hash), 64)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ProcessingJobModel)), 1
            )

    async def test_schema_invalid_output_is_never_indexed(self):
        settings = self.settings.model_copy(
            update={"AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS": 1}
        )
        ai = FakeAi({"wrong": True})
        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=ai,
            settings=settings,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )
        self.assertEqual(outcome.status, "non_retryable_failure")
        with self.factory() as session:
            from app.modules.ai_metadata.model import AssetAiAnalysisModel
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            self.assertEqual(analysis.status, "failed")
            self.assertIsNone(analysis.search_projection)
            self.assertTrue(analysis.validation_errors_json)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ProcessingJobModel)), 0
            )

    async def test_safety_limit_failure_is_never_indexed(self):
        from app.modules.ai_metadata.validator import MetadataDocumentValidator
        ai = FakeAi({"subject": "value exceeds limit"})
        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=ai,
            settings=self.settings.model_copy(
                update={"AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS": 1}
            ),
            validator=MetadataDocumentValidator(max_string_length=5),
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )
        self.assertEqual(outcome.status, "non_retryable_failure")
        with self.factory() as session:
            from app.modules.ai_metadata.model import AssetAiAnalysisModel
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            self.assertEqual(
                analysis.validation_errors_json[0]["code"],
                "max_string_length",
            )
            self.assertIsNone(analysis.search_projection)

    async def test_second_worker_cannot_claim_live_analysis(self):
        with self.factory() as session:
            repository = AiMetadataRepository(session)
            first = repository.claim_analysis(
                self.analysis_id, worker_id="worker-a", lease_seconds=60
            )
            session.commit()
        with self.factory() as session:
            second = AiMetadataRepository(session).claim_analysis(
                self.analysis_id, worker_id="worker-b", lease_seconds=60
            )
            session.rollback()
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    async def test_feature_flags_prevent_provider_call(self):
        ai = FakeAi({"subject": "cat"})
        disabled = Settings(GEMINI_API_KEY=None)
        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=ai,
            settings=disabled,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )
        self.assertEqual(outcome.error_code, "ai_single_analysis_disabled")
        self.assertEqual(ai.calls, 0)
