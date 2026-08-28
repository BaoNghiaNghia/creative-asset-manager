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
    AiProviderError,
    StoredAssetReadStream,
)
from app.providers.ai.gemini import (
    GeminiModelUnavailable,
    GeminiPoolTemporarilyUnavailable,
)
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.service import AiAnalysisService
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.ai_governance.model import AiCostRateModel, AiBudgetReservationModel, AiModelRateLimitStateModel, GeminiProjectQuotaStateModel
from datetime import datetime, timedelta, timezone


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
    provider_name = "gemini"
    model = "gemini-2.5-flash"
    def __init__(self, metadata, on_call=None):
        self.metadata = metadata
        self.on_call = on_call
        self.calls = 0
        self.last_input = None

    async def analyze_single(self, _input):
        self.calls += 1
        self.last_input = _input
        if self.on_call is not None:
            self.on_call()
        return AiMetadataAnalysisResult(
            metadata=self.metadata,
            provider="fake",
            model=self.model,
            provider_request_id="request-1",
            usage={"total": 1},
            provider_metadata={"finish_reason": "STOP"},
            raw_response={"safe": "audit"},
        )


class FailoverErrorAi(FakeAi):
    async def analyze_single(self, _input):
        self.calls += 1
        raise AiProviderError(
            "No Gemini model is currently available.",
            code="gemini_model_pool_exhausted",
            retryable=True,
            details={
                "requested_model": self.model,
                "actual_model": None,
                "attempted_models": ["gemini-first", "gemini-second"],
                "failover_reason": "gemini-first:daily_quota_exhausted",
            },
        )


class TemporaryPoolAi(FakeAi):
    def __init__(self, reason: str):
        super().__init__({"subject": "cat"})
        self.reason = reason

    async def analyze_single(self, _input):
        self.calls += 1
        retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        raise GeminiPoolTemporarilyUnavailable(
            attempted_models=["gemini-first"],
            reasons_by_model={
                "gemini-first": GeminiModelUnavailable(
                    model="gemini-first",
                    reason=self.reason,
                    available_at=retry_at,
                )
            },
            earliest_retry_at=retry_at,
        )


class PermanentGeminiErrorAi(FakeAi):
    async def analyze_single(self, _input):
        self.calls += 1
        raise AiProviderError(
            "Gemini API key is invalid.",
            code="gemini_http_error",
            retryable=False,
            status_code=401,
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
            session.add(AiCostRateModel(provider="gemini", model="gemini-2.5-flash", processing_mode="single", effective_at=datetime.now(timezone.utc) - timedelta(days=1), input_unit_cost=0, output_unit_cost=0, media_unit_cost=0))
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
            GEMINI_MODEL=FakeAi.model,
        )

    def tearDown(self):
        self.engine.dispose()

    async def test_unstored_source_requires_explicit_fallback(self):
        with self.factory() as session:
            session.query(AssetStorageObjectModel).delete()
            session.commit()
        ai = FakeAi({"subject": "cat"})

        blocked = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=ai,
            settings=self.settings,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )

        self.assertEqual(blocked.status, "retryable_failure")
        self.assertEqual(blocked.error_code, "managed_asset_not_stored")
        self.assertEqual(ai.calls, 0)

    async def test_explicit_unstored_source_is_prepared_and_analyzed(self):
        from app.modules.ai_metadata.model import AssetAiAnalysisModel

        with self.factory() as session:
            session.query(AssetStorageObjectModel).delete()
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            analysis.status = "pending"
            analysis.failure_retryable = None
            analysis.error_code = None
            analysis.error_message = None
            session.commit()
        ai = FakeAi({"subject": "cat"})

        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=ai,
            settings=self.settings,
            allow_unstored_source=True,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(ai.calls, 1)
        self.assertEqual(ai.last_input.image_mime_type, "image/jpeg")

    async def test_temporary_gemini_pool_exhaustion_defers_without_failure_or_usage(self):
        from app.modules.ai_governance.model import AiUsageRecordModel
        from app.modules.ai_metadata.model import AssetAiAnalysisModel

        for reason in ("rpm_exhausted", "tpm_exhausted", "rpd_exhausted", "cooldown"):
            with self.subTest(reason=reason):
                ai = TemporaryPoolAi(reason)
                outcome = await AiAnalysisService(
                    session_factory=self.factory,
                    storage_provider=self.storage,
                    ai_provider=ai,
                    settings=self.settings,
                ).analyze(
                    tenant_id="tenant-a",
                    analysis_id=self.analysis_id,
                    worker_id="worker-a",
                )
                self.assertEqual(outcome.status, "deferred")
                self.assertEqual(outcome.error_code, "gemini_model_pool_temporarily_unavailable")
                self.assertIsNotNone(outcome.retry_at)
                self.assertEqual(
                    outcome.metadata["reasons_by_model"]["gemini-first"]["reason"],
                    reason,
                )
                with self.factory() as session:
                    analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
                    self.assertEqual(analysis.status, "pending")
                    self.assertTrue(analysis.failure_retryable)
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(AiUsageRecordModel)),
                        0,
                    )
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(ProcessingJobModel)),
                        0,
                    )

    async def test_claim_reserved_model_slot_is_reused_without_second_reservation(self):
        from app.modules.processing_policy.claim import AI_MODEL_SLOT_PAYLOAD_KEY

        reserved_at = datetime.now(timezone.utc)
        preferred_model = self.settings.gemini_model_pool[1]
        next_eligible_at = reserved_at + timedelta(seconds=10)
        with self.factory() as session:
            session.add(
                AiCostRateModel(
                    provider="gemini",
                    model=preferred_model,
                    processing_mode="single",
                    effective_at=reserved_at - timedelta(days=1),
                    input_unit_cost=0,
                    output_unit_cost=0,
                    media_unit_cost=0,
                )
            )
            session.add(
                AiModelRateLimitStateModel(
                    tenant_id="tenant-a",
                    provider="gemini",
                    model=preferred_model,
                    last_started_at=reserved_at,
                    next_eligible_at=next_eligible_at,
                    blocked_until=None,
                )
            )
            job = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_pipeline",
                entity_id="pipeline-claimed-slot",
                idempotency_key="claimed-slot",
                status="processing",
                attempt_count=1,
                claimed_by="worker-a",
                claimed_at=reserved_at,
                lease_expires_at=reserved_at + timedelta(minutes=1),
                payload_json={
                    "analysis_id": self.analysis_id,
                    AI_MODEL_SLOT_PAYLOAD_KEY: {
                        "provider": "gemini",
                        "model": preferred_model,
                        "reserved_at": reserved_at.isoformat(),
                        "next_eligible_at": next_eligible_at.isoformat(),
                        "attempt_count": 1,
                        "worker_id": "worker-a",
                    }
                },
            )
            session.add(job)
            session.commit()
            job_id = job.id

        ai = FakeAi({"subject": "cat"})
        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=ai,
            settings=self.settings,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
            job_id=job_id,
        )

        self.assertEqual(outcome.status, "completed")
        self.assertNotEqual(outcome.error_code, "gemini_model_pool_exhausted")
        self.assertEqual(ai.calls, 1)
        self.assertEqual(ai.last_input.preferred_model, preferred_model)
        with self.factory() as session:
            state = session.get(
                AiModelRateLimitStateModel,
                {
                    "tenant_id": "tenant-a",
                    "provider": "gemini",
                    "model": preferred_model,
                },
            )
            self.assertEqual(
                state.next_eligible_at.replace(tzinfo=timezone.utc), next_eligible_at
            )

    async def test_local_model_delay_does_not_reserve_budget_or_project_quota(self):
        from app.modules.ai_governance.model import AiUsageRecordModel
        from app.modules.ai_metadata.model import AssetAiAnalysisModel

        now = datetime.now(timezone.utc)
        with self.factory() as session:
            session.add(AiModelRateLimitStateModel(
                tenant_id="tenant-a",
                provider="gemini",
                model=FakeAi.model,
                last_started_at=now,
                next_eligible_at=now + timedelta(minutes=2),
                blocked_until=None,
            ))
            session.commit()

        settings = self.settings.model_copy(update={
            "AI_MODEL_RPM_LIMITS": '{"gemini":{"gemini-2.5-flash":4}}',
        })
        ai = FakeAi({"subject": "cat"})
        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=ai,
            settings=settings,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
            job_id="job-local-delay",
        )

        self.assertEqual(outcome.status, "deferred")
        self.assertEqual(outcome.error_code, "ai_model_rate_limited")
        self.assertIsNotNone(outcome.retry_at)
        self.assertEqual(ai.calls, 0)
        with self.factory() as session:
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            state = session.get(
                AiModelRateLimitStateModel,
                {"tenant_id": "tenant-a", "provider": "gemini", "model": FakeAi.model},
            )
            self.assertEqual(analysis.status, "pending")
            self.assertEqual(analysis.attempt_count, 0)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(AiBudgetReservationModel)),
                0,
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(AiUsageRecordModel)),
                0,
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(GeminiProjectQuotaStateModel)),
                0,
            )
            self.assertIsNone(state.blocked_until)
            self.assertGreater(state.next_eligible_at.replace(tzinfo=timezone.utc), now)

    async def test_provider_error_releases_budget_reservation(self):
        from app.modules.ai_governance.model import AiBudgetReservationModel

        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=PermanentGeminiErrorAi({"subject": "cat"}),
            settings=self.settings,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
            job_id="job-terminal",
        )
        self.assertEqual(outcome.status, "non_retryable_failure")
        with self.factory() as session:
            reservation = session.scalar(select(AiBudgetReservationModel))
            self.assertIsNotNone(reservation)
            self.assertEqual(reservation.status, "released")
            self.assertEqual(reservation.denial_reason, "gemini_http_error")

    async def test_permanent_gemini_error_remains_failed(self):
        from app.modules.ai_metadata.model import AssetAiAnalysisModel

        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=PermanentGeminiErrorAi({"subject": "cat"}),
            settings=self.settings,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )

        self.assertEqual(outcome.status, "non_retryable_failure")
        with self.factory() as session:
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            self.assertEqual(analysis.status, "failed")

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
        disabled = self.settings.model_copy(
            update={"AI_SINGLE_ANALYSIS_ENABLED": False}
        )
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


    async def test_budget_breaker_blocks_before_provider_invocation(self):
        from datetime import datetime, timezone
        from app.modules.ai_governance.model import (
            AiCostRateModel, AiUsageRecordModel, TenantAiBudgetPolicyModel,
        )
        from app.modules.ai_metadata.model import AssetAiAnalysisModel
        with self.factory() as session:
            session.add(AiCostRateModel(
                provider="gemini", model=self.settings.GEMINI_MODEL, processing_mode="single",
                effective_at=datetime.now(timezone.utc),
                input_unit_cost=0, output_unit_cost=0, media_unit_cost=.001,
                currency="USD",
            ))
            session.add(TenantAiBudgetPolicyModel(
                tenant_id="tenant-a", enabled=True, daily_limit_micros=500,
                warning_threshold_percent=80, hard_stop_threshold_percent=100,
            ))
            session.commit()
        ai = FakeAi({"subject": "cat"})
        outcome = await AiAnalysisService(
            session_factory=self.factory, storage_provider=self.storage,
            ai_provider=ai, settings=self.settings,
        ).analyze(tenant_id="tenant-a", analysis_id=self.analysis_id, worker_id="worker-a")
        self.assertEqual(outcome.status, "budget_blocked")
        self.assertEqual(ai.calls, 0)
        with self.factory() as session:
            analysis=session.get(AssetAiAnalysisModel,self.analysis_id)
            self.assertEqual(analysis.status,"budget_blocked")
            usage=session.scalar(select(AiUsageRecordModel))
            self.assertEqual(usage.outcome,"budget_blocked")

    async def test_lost_analysis_lease_never_completes_result(self):
        from datetime import datetime, timedelta, timezone
        from app.modules.ai_metadata.model import AssetAiAnalysisModel

        def expire_lease():
            with self.factory() as session:
                analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
                analysis.claimed_by = "worker-b"
                analysis.lease_expires_at = (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                )
                session.commit()

        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=FakeAi({"subject": "cat"}, on_call=expire_lease),
            settings=self.settings,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )

        self.assertEqual(outcome.status, "cancelled")
        self.assertEqual(outcome.error_code, "analysis_lease_lost")
        with self.factory() as session:
            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            self.assertNotEqual(analysis.status, "completed")
            self.assertIsNone(analysis.metadata_json)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ProcessingJobModel)),
                0,
            )


    async def test_missing_cost_rate_fails_closed_before_provider(self):
        from sqlalchemy import delete
        with self.factory() as session:
            session.execute(delete(AiCostRateModel))
            session.commit()
        ai = FakeAi({"subject": "cat"})
        outcome = await AiAnalysisService(
            session_factory=self.factory, storage_provider=self.storage,
            ai_provider=ai, settings=self.settings,
        ).analyze(
            tenant_id="tenant-a", analysis_id=self.analysis_id,
            worker_id="worker-a",
        )
        self.assertEqual(outcome.status, "budget_blocked")
        self.assertEqual(outcome.error_code, "missing_cost_rate")
        self.assertEqual(ai.calls, 0)

    async def test_provider_failover_audit_is_persisted_on_terminal_provider_error(self):
        outcome = await AiAnalysisService(
            session_factory=self.factory,
            storage_provider=self.storage,
            ai_provider=FailoverErrorAi({"subject": "cat"}),
            settings=self.settings,
        ).analyze(
            tenant_id="tenant-a",
            analysis_id=self.analysis_id,
            worker_id="worker-a",
        )

        self.assertEqual(outcome.status, "retryable_failure")
        self.assertEqual(outcome.error_code, "gemini_model_pool_exhausted")
        with self.factory() as session:
            from app.modules.ai_metadata.model import AssetAiAnalysisModel

            analysis = session.get(AssetAiAnalysisModel, self.analysis_id)
            self.assertEqual(
                analysis.provider_metadata_json["attempted_models"],
                ["gemini-first", "gemini-second"],
            )
            self.assertEqual(analysis.provider_metadata_json["actual_model"], None)

    async def test_privileged_missing_rate_override_is_audited_and_does_not_report_zero(self):
        from sqlalchemy import delete
        from app.modules.ai_governance.model import AiUsageRecordModel
        from app.modules.ai_governance.repository import AiGovernanceRepository
        with self.factory() as session:
            session.execute(delete(AiCostRateModel))
            AiGovernanceRepository(session).grant_budget_override(
                "tenant-a", self.analysis_id, "platform-admin", "approved exception")
            session.commit()
        ai = FakeAi({"subject": "cat"})
        outcome = await AiAnalysisService(
            session_factory=self.factory, storage_provider=self.storage,
            ai_provider=ai, settings=self.settings,
        ).analyze(
            tenant_id="tenant-a", analysis_id=self.analysis_id,
            worker_id="worker-a",
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(ai.calls, 1)
        with self.factory() as session:
            usage = session.scalar(select(AiUsageRecordModel))
            self.assertIsNone(usage.locally_estimated_cost_micros)
