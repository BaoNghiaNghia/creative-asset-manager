import asyncio
import time
import unittest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.ai_metadata import model as _ai_metadata_models  # noqa: F401
from app.modules.assets import model as _asset_models  # noqa: F401
from app.modules.search.governance_model import SearchShadowObservationModel, TenantSearchShadowPolicyModel
from app.modules.search.shadow import SearchShadowComparator, SearchShadowMetrics, SearchShadowRepository


class FakeSearchProvider:
    def __init__(self, result=None, error=None, delay=0):
        self.result = result or {"items": [], "total": 0}
        self.error = error
        self.delay = delay
        self.calls = 0

    async def search(self):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


class ShadowSearchRemediationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add(TenantSearchShadowPolicyModel(
                tenant_id="tenant-a", enabled=True, primary_version="v1",
                shadow_version="v2", sample_percentage=100, timeout_ms=15,
                top_k=2, persist_raw_query=False,
            ))
            session.commit()
        self.metrics = SearchShadowMetrics()
        self.comparator = SearchShadowComparator(
            session_factory=self.sessions, global_enabled=lambda: True,
            max_timeout_ms=20, metrics=self.metrics,
        )

    async def asyncTearDown(self):
        await self.comparator.shutdown(.05)
        self.engine.dispose()

    async def test_primary_never_depends_on_shadow_or_policy_success(self):
        shadow = FakeSearchProvider(error=RuntimeError("provider detail"))
        primary = {"items": [{"id": "primary"}], "total": 1}
        result = await self.comparator.execute(
            tenant_id="tenant-a", query="cat",
            primary=lambda: asyncio.sleep(0, result=primary), shadow=shadow.search,
            primary_version="v1", shadow_version="v2", surface="explorer_search",
        )
        self.assertEqual(result, primary)
        await self.comparator.drain()
        with self.sessions() as session:
            row = session.scalar(select(SearchShadowObservationModel))
            self.assertEqual(row.error_category, "provider_error")
            self.assertIsNone(row.raw_query)

        broken = SearchShadowComparator(
            session_factory=lambda: (_ for _ in ()).throw(RuntimeError("db")),
            global_enabled=lambda: True, max_timeout_ms=10,
        )
        result = await broken.execute(
            tenant_id="tenant-a", query="cat",
            primary=lambda: asyncio.sleep(0, result=primary),
            shadow=shadow.search,
        )
        self.assertEqual(result, primary)
        self.assertEqual(shadow.calls, 1)

    async def test_timeout_sampling_direction_and_shutdown_are_bounded(self):
        slow = FakeSearchProvider(delay=.1)
        primary = {"items": [{"id": "a"}], "total": 1}
        started = time.perf_counter()
        await self.comparator.execute(
            tenant_id="tenant-a", query="slow",
            primary=lambda: asyncio.sleep(0, result=primary), shadow=slow.search,
            primary_version="v1", shadow_version="v2", surface="explorer_search",
        )
        self.assertLess(time.perf_counter() - started, .01)
        await asyncio.sleep(.03)
        with self.sessions() as session:
            row = session.scalar(select(SearchShadowObservationModel))
            self.assertEqual(row.error_category, "timeout")
            self.assertLessEqual(row.shadow_latency_ms, 15)

        mismatch = FakeSearchProvider()
        scheduled = await self.comparator.observe(
            tenant_id="tenant-a", query="direction", primary_result=primary,
            primary_ms=1, shadow=mismatch.search, primary_version="v2",
            shadow_version="v1", surface="search_v2",
        )
        self.assertFalse(scheduled)
        self.assertEqual(mismatch.calls, 0)
        started = time.perf_counter()
        await self.comparator.shutdown(.01)
        self.assertLess(time.perf_counter() - started, .05)

    async def test_standard_overlap_counts_latencies_reports_and_tenant_isolation(self):
        primary = {"items": [{"id": "a"}, {"id": "b"}], "total": 2}
        shadow = FakeSearchProvider(
            {"items": [{"id": "a"}, {"id": "c"}], "total": 4}
        )
        await self.comparator.execute(
            tenant_id="tenant-a", query="cat mama",
            primary=lambda: asyncio.sleep(.005, result=primary),
            shadow=shadow.search, primary_version="v1", shadow_version="v2",
            surface="explorer_search", metadata_profile="general",
        )
        await self.comparator.drain()
        with self.sessions() as session:
            row = session.scalar(select(SearchShadowObservationModel))
            self.assertEqual(row.top_k_overlap, .5)
            self.assertTrue(row.top_result_agrees)
            self.assertFalse(row.zero_result_disagrees)
            self.assertEqual(row.shadow_count - row.primary_count, 2)
            self.assertGreaterEqual(row.primary_latency_ms, 1)
            report = SearchShadowRepository(session).report(
                "tenant-a", metadata_profile="general",
                primary_version="v1", shadow_version="v2",
            )
            self.assertEqual(report["observations"], 1)
            self.assertEqual(report["top_1_agreement_rate"], 1)
            self.assertEqual(report["average_result_count_difference"], 2)
            self.assertIn("p95", report["primary_latency_ms"])
            self.assertEqual(
                SearchShadowRepository(session).report("tenant-b")["observations"], 0
            )

        snapshot = self.metrics.snapshot()
        serialized = repr(snapshot)
        self.assertNotIn("tenant-a", serialized)
        self.assertNotIn("cat mama", serialized)


if __name__ == "__main__":
    unittest.main()
