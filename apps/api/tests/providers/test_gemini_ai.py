import json
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncio
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.providers.contracts import AiMetadataAnalysisInput, AiProviderError
from app.modules.ai_governance.model import GeminiProjectQuotaStateModel
from app.providers.ai.factory import _DatabaseGeminiQuotaCoordinator
from app.providers.ai.gemini import (
    GeminiAiMetadataProvider,
    GeminiModelLimit,
    GeminiPoolTemporarilyUnavailable,
)


def analysis_input():
    return AiMetadataAnalysisInput(
        tenant_id="tenant-a",
        asset_id="asset-a",
        prompt="Return metadata",
        image_bytes=b"jpeg",
        image_mime_type="image/jpeg",
        metadata_profile="general",
        metadata_profile_version="1",
    )


async def _immediate():
    return None


def configured_gemini_provider(
    api_key: str,
    *,
    model: str = "gemini-2.5-flash",
    model_pool: tuple[str, ...] | None = None,
    model_limits: dict[str, GeminiModelLimit] | None = None,
    **kwargs,
) -> GeminiAiMetadataProvider:
    pool = model_pool or (model,)
    return GeminiAiMetadataProvider(
        api_key,
        model=model,
        model_pool=model_pool,
        model_limits=model_limits or {
            name: GeminiModelLimit(rpm=12, tpm=200000, rpd=400)
            for name in pool
        },
        **kwargs,
    )


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=self.seconds
        )

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


class GeminiAiMetadataProviderTest(unittest.IsolatedAsyncioTestCase):
    def test_missing_model_limits_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "gemini-first, gemini-second",
        ):
            GeminiAiMetadataProvider(
                "secret",
                model="gemini-first",
                model_pool=("gemini-first", "gemini-second"),
            )

    def test_tuple_model_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "must use GeminiModelLimit values"):
            GeminiAiMetadataProvider(
                "secret",
                model="gemini-test",
                model_limits={"gemini-test": (12, 400)},  # type: ignore[dict-item]
            )
    async def test_builds_structured_multimodal_request_and_captures_audit(self):
        async def handler(request):
            self.assertNotIn("key=", str(request.url))
            self.assertEqual(request.headers["x-goog-api-key"], "secret")
            body = json.loads(request.content)
            self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
            self.assertEqual(body["contents"][0]["parts"][0]["text"], "Return metadata")
            return httpx.Response(
                200,
                json={
                    "candidates": [{
                        "content": {"parts": [{"text": '{"subject":"cat"}'}]},
                        "finishReason": "STOP",
                    }],
                    "usageMetadata": {"totalTokenCount": 9},
                    "modelVersion": "gemini-test",
                    "responseId": "request-1",
                },
            )

        provider = configured_gemini_provider(
            "secret", model="gemini-test",
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())
        self.assertEqual(result.metadata, {"subject": "cat"})
        self.assertEqual(result.provider_request_id, "request-1")
        self.assertEqual(result.usage["totalTokenCount"], 9)

    async def test_malformed_and_empty_output_are_permanent_provider_errors(self):
        for payload, code in (
            ({"candidates": [{"content": {"parts": [{"text": "nope"}]}}]}, "gemini_invalid_json"),
            ({"candidates": []}, "gemini_empty_response"),
        ):
            async def handler(_request, payload=payload):
                return httpx.Response(200, json=payload)
            provider = configured_gemini_provider(
                "secret", transport=httpx.MockTransport(handler)
            )
            with self.assertRaises(AiProviderError) as raised:
                await provider.analyze_single(analysis_input())
            self.assertEqual(raised.exception.code, code)
            self.assertFalse(raised.exception.retryable)
            self.assertNotIsInstance(
                raised.exception, GeminiPoolTemporarilyUnavailable
            )

    async def test_rate_limit_is_retryable_and_bad_request_is_permanent(self):
        delays = []

        async def sleeper(delay):
            delays.append(delay)

        for status, retryable in ((429, True), (400, False)):
            async def handler(_request, status=status):
                return httpx.Response(status, json={"error": {}})
            provider = configured_gemini_provider(
                "secret",
                sleeper=sleeper,
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(AiProviderError) as raised:
                await provider.analyze_single(analysis_input())
            self.assertIs(raised.exception.retryable, retryable)

        self.assertEqual(delays, [])

    async def test_404_diagnostics_are_sanitized_and_model_not_found_fails_over(self):
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                return httpx.Response(
                    404,
                    headers={"x-goog-request-id": "google-request-1"},
                    json={
                        "error": {
                            "status": "NOT_FOUND",
                            "message": "Model gemini-first was not found; key=secret",
                        }
                    },
                )
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(
            AiMetadataAnalysisInput(
                tenant_id="tenant-a", asset_id="asset-a", prompt="Return metadata",
                image_bytes=b"jpeg", image_mime_type="image/jpeg",
                metadata_profile="general", metadata_profile_version="1",
                analysis_id="analysis-1", pipeline_id="pipeline-1",
            )
        )
        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(result.model, "gemini-second")
        self.assertIn("gemini-first:cooldown", result.provider_metadata["failover_reason"])

    async def test_404_input_error_is_terminal_and_redacts_key(self):
        async def handler(_request):
            return httpx.Response(
                404,
                json={"error": {
                    "status": "NOT_FOUND",
                    "message": "Input image resource was not found; api_key=secret",
                }},
            )

        provider = configured_gemini_provider(
            "secret", transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(AiProviderError) as raised:
            await provider.analyze_single(
                AiMetadataAnalysisInput(
                    tenant_id="tenant-a", asset_id="asset-a", prompt="Return metadata",
                    image_bytes=b"jpeg", image_mime_type="image/jpeg",
                    metadata_profile="general", metadata_profile_version="1",
                    analysis_id="analysis-1", pipeline_id="pipeline-1",
                )
            )
        self.assertEqual(raised.exception.code, "gemini_input_resource_not_found")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.details["analysis_id"], "analysis-1")
        self.assertEqual(raised.exception.details["pipeline_id"], "pipeline-1")
        self.assertNotIn("secret", str(raised.exception.details))

    async def test_timeout_is_retryable(self):
        async def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)
        provider = configured_gemini_provider(
            "secret", transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(AiProviderError) as raised:
            await provider.analyze_single(analysis_input())
        self.assertEqual(raised.exception.code, "gemini_transport_error")
        self.assertTrue(raised.exception.retryable)

    async def test_daily_quota_fails_over_in_priority_order_and_records_audit(self):
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                return httpx.Response(
                    429,
                    json={"error": {"message": "quota exceeded per day"}},
                )
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={"gemini-first": GeminiModelLimit(rpm=12, tpm=100, rpd=400), "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=400)},
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(result.model, "gemini-second")
        self.assertEqual(result.provider_metadata["requested_model"], "gemini-first")
        self.assertEqual(result.provider_metadata["actual_model"], "gemini-second")
        self.assertEqual(result.provider_metadata["attempted_models"], ["gemini-first", "gemini-second"])
        self.assertIn("rpd_exhausted", result.provider_metadata["failover_reason"])

    async def test_rate_limit_tries_next_model_without_sleep(self):
        seen = []
        delays = []
        clock = FakeClock()

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                return httpx.Response(
                    429,
                    headers={"retry-after": "30"},
                    json={"error": {"message": "rate limit per minute"}},
                )
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        async def sleeper(delay):
            delays.append(delay)

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=12, tpm=100, rpd=400),
                "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=400),
            },
            sleeper=sleeper,
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(delays, [])
        self.assertEqual(result.model, "gemini-second")
        self.assertIn("gemini-first:rpm_exhausted", result.provider_metadata["failover_reason"])

    async def test_tpm_limit_uses_next_available_model(self):
        seen = []
        clock = FakeClock()

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=12, tpm=4, rpd=400),
                "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=400),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-second"])
        self.assertEqual(result.model, "gemini-second")
        self.assertIn("gemini-first:tpm_exhausted", result.provider_metadata["failover_reason"])

    async def test_rpm_limit_uses_next_available_model(self):
        seen = []
        clock = FakeClock()

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=1, tpm=100, rpd=400),
                "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=400),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )

        await provider.analyze_single(analysis_input())
        second = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(second.model, "gemini-second")
        self.assertIn(
            "gemini-first:rpm_exhausted",
            second.provider_metadata["failover_reason"],
        )
    async def test_tpm_reservation_reconciles_prompt_usage(self):
        seen = []
        clock = FakeClock()

        async def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "usageMetadata": {"promptTokenCount": 1},
                "modelVersion": "gemini-first",
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=2, tpm=6, rpd=2),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )

        await provider.analyze_single(analysis_input())
        await provider.analyze_single(analysis_input())

        self.assertEqual(len(seen), 2)
        self.assertEqual(
            [item.tokens for item in provider._runtime["gemini-first"].recent_input_tokens],
            [1, 1],
        )

    async def test_tpm_reservation_expires_from_the_rolling_window(self):
        seen = []
        clock = FakeClock()

        async def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": "gemini-first",
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=2, tpm=5, rpd=2),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )

        await provider.analyze_single(analysis_input())
        clock.advance(60)
        await provider.analyze_single(analysis_input())

        self.assertEqual(len(seen), 2)
    async def test_bad_request_does_not_fail_over(self):
        seen = []

        async def handler(request):
            seen.append(request.url.path)
            return httpx.Response(400, json={"error": {"message": "bad image"}})

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(AiProviderError) as raised:
            await provider.analyze_single(analysis_input())

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(len(seen), 1)
        self.assertEqual(raised.exception.details["attempted_models"], ["gemini-first"])

    async def test_per_model_daily_limit_uses_next_model_without_job_retry(self):
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={"gemini-first": GeminiModelLimit(rpm=12, tpm=100, rpd=1), "gemini-second": GeminiModelLimit(rpm=12, tpm=100, rpd=1)},
            transport=httpx.MockTransport(handler),
        )
        await provider.analyze_single(analysis_input())
        second = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(second.model, "gemini-second")

    async def test_service_unavailable_cools_down_model_and_uses_next_model(self):
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                return httpx.Response(503, json={"error": {"message": "unavailable"}})
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            transport=httpx.MockTransport(handler),
        )
        result = await provider.analyze_single(analysis_input())

        self.assertEqual(seen, ["gemini-first", "gemini-second"])
        self.assertEqual(result.model, "gemini-second")
        self.assertIn("gemini-first:cooldown", result.provider_metadata["failover_reason"])

    async def test_only_one_request_per_model_is_in_flight(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        seen = []

        async def handler(request):
            model = request.url.path.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model == "gemini-first":
                first_started.set()
                await release_first.wait()
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
                "modelVersion": model,
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            transport=httpx.MockTransport(handler),
        )
        first = asyncio.create_task(provider.analyze_single(analysis_input()))
        await first_started.wait()
        second = await provider.analyze_single(analysis_input())
        release_first.set()
        await first

        self.assertEqual(second.model, "gemini-second")
        self.assertEqual(seen.count("gemini-first"), 1)
        self.assertEqual(seen.count("gemini-second"), 1)


    async def test_all_rpm_limited_models_return_earliest_retry(self):
        clock = FakeClock()

        async def handler(_request):
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=1, tpm=100, rpd=10),
                "gemini-second": GeminiModelLimit(rpm=1, tpm=100, rpd=10),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )
        await provider.analyze_single(analysis_input())
        await provider.analyze_single(analysis_input())
        with self.assertRaises(GeminiPoolTemporarilyUnavailable) as raised:
            await provider.analyze_single(analysis_input())

        error = raised.exception
        self.assertEqual(error.reasons_by_model["gemini-first"].reason, "rpm_exhausted")
        self.assertEqual(error.reasons_by_model["gemini-second"].reason, "rpm_exhausted")
        self.assertEqual(error.earliest_retry_at, clock.now() + timedelta(seconds=60))
        self.assertEqual(error.provider, "gemini")
        self.assertEqual(error.details["provider"], "gemini")

    async def test_all_tpm_limited_models_return_earliest_retry(self):
        clock = FakeClock()

        async def handler(_request):
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=10, tpm=6, rpd=10),
                "gemini-second": GeminiModelLimit(rpm=10, tpm=6, rpd=10),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )
        await provider.analyze_single(analysis_input())
        await provider.analyze_single(analysis_input())
        with self.assertRaises(GeminiPoolTemporarilyUnavailable) as raised:
            await provider.analyze_single(analysis_input())

        self.assertEqual(
            {item.reason for item in raised.exception.reasons_by_model.values()},
            {"tpm_exhausted"},
        )
        self.assertEqual(raised.exception.earliest_retry_at, clock.now() + timedelta(seconds=60))

    async def test_project_cap_defers_without_a_provider_call_and_persists_across_restart(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        now = datetime(2040, 1, 1, tzinfo=timezone.utc)
        with sessions() as session:
            session.add(GeminiProjectQuotaStateModel(
                quota_scope="creative-assets",
                model="__project_total__",
                quota_day=now.astimezone(ZoneInfo("America/Los_Angeles")).date(),
                reserved_requests=2,
                updated_at=now,
            ))
            session.commit()
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
            })

        def provider():
            return configured_gemini_provider(
                "secret",
                model="gemini-test",
                model_pool=("gemini-test",),
                model_limits={"gemini-test": GeminiModelLimit(rpm=10, tpm=100, rpd=10)},
                now=lambda: now,
                quota_coordinator=_DatabaseGeminiQuotaCoordinator(
                    sessions, "creative-assets", 2
                ),
                transport=httpx.MockTransport(handler),
            )

        pacific = ZoneInfo("America/Los_Angeles")
        expected_retry_at = datetime.combine(
            now.astimezone(pacific).date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=pacific,
        ).astimezone(timezone.utc)
        with self.assertLogs("cam.gemini_quota", level="WARNING") as logs:
            for runtime in (provider(), provider()):
                with self.assertRaises(GeminiPoolTemporarilyUnavailable) as raised:
                    await runtime.analyze_single(analysis_input())
                self.assertEqual(
                    {item.reason for item in raised.exception.reasons_by_model.values()},
                    {"project_rpd_exhausted"},
                )
                self.assertEqual(raised.exception.earliest_retry_at, expected_retry_at)
        self.assertEqual(calls, 0)
        self.assertEqual(len(logs.records), 2)
        for record in logs.records:
            self.assertEqual(record.msg, "gemini_project_daily_cap_deferred")
            self.assertEqual(record.quota_scope, "creative-assets")
            self.assertEqual(record.project_reserved_requests, 2)
            self.assertEqual(record.project_daily_limit, 2)
            self.assertEqual(record.model, "gemini-test")
            self.assertFalse(record.provider_call_started)
        with sessions() as session:
            project = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "__project_total__"},
            )
            model = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "gemini-test"},
            )
        self.assertEqual(project.reserved_requests, 2)
        self.assertIsNone(model)
        engine.dispose()

    async def test_project_cap_last_slot_authorizes_one_provider_call(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        now = datetime(2040, 1, 1, tzinfo=timezone.utc)
        with sessions() as session:
            session.add(GeminiProjectQuotaStateModel(
                quota_scope="creative-assets",
                model="__project_total__",
                quota_day=now.astimezone(ZoneInfo("America/Los_Angeles")).date(),
                reserved_requests=1,
                updated_at=now,
            ))
            session.commit()
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-test",
            model_pool=("gemini-test",),
            model_limits={"gemini-test": GeminiModelLimit(rpm=10, tpm=100, rpd=10)},
            now=lambda: now,
            quota_coordinator=_DatabaseGeminiQuotaCoordinator(
                sessions, "creative-assets", 2
            ),
            transport=httpx.MockTransport(handler),
        )
        await provider.analyze_single(analysis_input())
        with self.assertRaises(GeminiPoolTemporarilyUnavailable):
            await provider.analyze_single(analysis_input())
        self.assertEqual(calls, 1)
        with sessions() as session:
            project = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "__project_total__"},
            )
            model = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "gemini-test"},
            )
        self.assertEqual(project.reserved_requests, 2)
        self.assertEqual(model.reserved_requests, 1)
        engine.dispose()

    async def test_concurrent_workers_only_start_one_provider_call_at_last_project_slot(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        now = datetime(2040, 1, 1, tzinfo=timezone.utc)
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
            })

        def provider():
            return configured_gemini_provider(
                "secret",
                model="gemini-test",
                model_pool=("gemini-test",),
                model_limits={"gemini-test": GeminiModelLimit(rpm=10, tpm=100, rpd=10)},
                now=lambda: now,
                quota_coordinator=_DatabaseGeminiQuotaCoordinator(
                    sessions, "creative-assets", 1
                ),
                transport=httpx.MockTransport(handler),
            )

        results = await asyncio.gather(
            provider().analyze_single(analysis_input()),
            provider().analyze_single(analysis_input()),
            return_exceptions=True,
        )
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        self.assertEqual(
            sum(isinstance(result, GeminiPoolTemporarilyUnavailable) for result in results),
            1,
        )
        self.assertEqual(calls, 1)
        with sessions() as session:
            project = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "__project_total__"},
            )
            model = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "gemini-test"},
            )
        self.assertEqual(project.reserved_requests, 1)
        self.assertEqual(model.reserved_requests, 1)
        engine.dispose()

    async def test_skipped_failover_model_does_not_consume_project_capacity(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        now = datetime(2040, 1, 1, tzinfo=timezone.utc)
        calls: list[str] = []

        async def handler(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=10, tpm=100, rpd=1),
                "gemini-second": GeminiModelLimit(rpm=10, tpm=100, rpd=10),
            },
            now=lambda: now,
            quota_coordinator=_DatabaseGeminiQuotaCoordinator(
                sessions, "creative-assets", 10
            ),
            transport=httpx.MockTransport(handler),
        )
        await provider.analyze_single(analysis_input())
        await provider.analyze_single(analysis_input())

        with sessions() as session:
            project = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "__project_total__"},
            )
            first = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "gemini-first"},
            )
            second = session.get(
                GeminiProjectQuotaStateModel,
                {"quota_scope": "creative-assets", "model": "gemini-second"},
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(project.reserved_requests, 2)
        self.assertEqual(first.reserved_requests, 1)
        self.assertEqual(second.reserved_requests, 1)
        engine.dispose()

    async def test_all_rpd_limited_models_return_next_pacific_reset(self):
        clock = FakeClock()

        async def handler(_request):
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '{"subject":"cat"}'}]}}],
            })

        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            model_limits={
                "gemini-first": GeminiModelLimit(rpm=10, tpm=100, rpd=1),
                "gemini-second": GeminiModelLimit(rpm=10, tpm=100, rpd=1),
            },
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(handler),
        )
        await provider.analyze_single(analysis_input())
        await provider.analyze_single(analysis_input())
        with self.assertRaises(GeminiPoolTemporarilyUnavailable) as raised:
            await provider.analyze_single(analysis_input())

        self.assertEqual(
            {item.reason for item in raised.exception.reasons_by_model.values()},
            {"rpd_exhausted"},
        )
        self.assertGreater(raised.exception.earliest_retry_at, clock.now())

    async def test_cooldown_and_in_flight_availability_use_earliest_retry(self):
        clock = FakeClock()
        provider = configured_gemini_provider(
            "secret",
            model="gemini-first",
            model_pool=("gemini-first", "gemini-second"),
            clock=clock,
            now=clock.now,
            transport=httpx.MockTransport(lambda _request: None),
        )
        first = provider._runtime["gemini-first"]
        second = provider._runtime["gemini-second"]
        first.cooldown_until = clock.now() + timedelta(seconds=30)
        second.in_flight = True

        with self.assertRaises(GeminiPoolTemporarilyUnavailable) as raised:
            await provider.analyze_single(analysis_input())

        error = raised.exception
        self.assertEqual(error.reasons_by_model["gemini-first"].reason, "cooldown")
        self.assertEqual(error.reasons_by_model["gemini-second"].reason, "in_flight")
        self.assertEqual(error.earliest_retry_at, clock.now() + timedelta(seconds=1))

    async def test_batch_submit_recovers_ambiguous_transport_by_display_name(self):
        import tempfile
        from pathlib import Path
        from app.domain.providers.contracts import AiBatchSubmissionInput

        list_calls=0
        async def handler(request):
            nonlocal list_calls
            if request.method=="GET":
                list_calls+=1
                if list_calls==1:
                    return httpx.Response(200,json={"operations":[]})
                return httpx.Response(200,json={"operations":[{
                    "name":"batches/recovered",
                    "response":{"displayName":"stable-name",
                        "state":"BATCH_STATE_PENDING"}
                }]})
            raise httpx.ReadTimeout("lost response",request=request)

        provider=configured_gemini_provider(
            "secret",model="gemini-test",transport=httpx.MockTransport(handler))
        with tempfile.NamedTemporaryFile("w",delete=False) as handle:
            json.dump({"custom_item_id":"item-1","prompt":"analyze",
                "image_mime_type":"image/jpeg","image_base64":"YWJj",
                "metadata_profile":"general","metadata_profile_version":"1"},handle)
            handle.write("\n");path=handle.name
        try:
            result=await provider.submit_batch(AiBatchSubmissionInput(
                "tenant-a","stable-key","stable-name","gemini-test",path,1,100))
            self.assertEqual(result.provider_batch_id,"batches/recovered")
            self.assertEqual(result.state,"pending")
        finally:
            Path(path).unlink(missing_ok=True)

    async def test_batch_submit_status_stream_and_cancel(self):
        import tempfile
        from pathlib import Path
        from app.domain.providers.contracts import (
            AiBatchResultsInput,AiBatchStatusInput,AiBatchSubmissionInput,
        )
        seen=[]
        async def handler(request):
            seen.append((request.method,str(request.url)))
            if request.method=="GET" and str(request.url).endswith("batches?pageSize=100"):
                return httpx.Response(200,json={"operations":[]})
            if request.method=="POST" and "batchGenerateContent" in str(request.url):
                body=json.loads(request.content)
                self.assertEqual(body["batch"]["inputConfig"]["requests"]["requests"][0]["metadata"]["key"],"item-1")
                return httpx.Response(200,json={"name":"batches/1","state":"JOB_STATE_PENDING"})
            if request.method=="GET":
                return httpx.Response(200,json={
                    "name":"batches/1",
                    "response":{
                        "name":"batches/1","state":"BATCH_STATE_SUCCEEDED",
                        "output":{"inlinedResponses":{"inlinedResponses":[{
                            "metadata":{"key":"item-1"},
                            "response":{"candidates":[{"content":{"parts":[{"text":'{"subject":"cat"}'}]}}],
                                "usageMetadata":{"promptTokenCount":2},"responseId":"r1"}
                        }]}}
                    }})
            return httpx.Response(200,json={})
        provider=configured_gemini_provider(
            "secret",model="gemini-test",transport=httpx.MockTransport(handler))
        with tempfile.NamedTemporaryFile("w",delete=False) as handle:
            json.dump({"custom_item_id":"item-1","prompt":"analyze",
                "image_mime_type":"image/jpeg","image_base64":"YWJj",
                "metadata_profile":"general","metadata_profile_version":"1"},handle)
            handle.write("\n");path=handle.name
        try:
            submitted=await provider.submit_batch(AiBatchSubmissionInput(
                "tenant-a","key","display","gemini-test",path,1,100))
            self.assertEqual(submitted.provider_batch_id,"batches/1")
            status=await provider.get_batch_status(AiBatchStatusInput(
                "tenant-a","batches/1"))
            self.assertEqual(status.state,"completed")
            results=[value async for value in provider.stream_batch_results(
                AiBatchResultsInput("tenant-a","batches/1"))]
            self.assertEqual(results[0].custom_item_id,"item-1")
            self.assertEqual(results[0].result.metadata,{"subject":"cat"})
            self.assertTrue(await provider.cancel_batch(AiBatchStatusInput(
                "tenant-a","batches/1")))
        finally:
            Path(path).unlink(missing_ok=True)
