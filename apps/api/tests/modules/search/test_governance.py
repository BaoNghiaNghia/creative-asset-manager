import asyncio
import time
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.search.active_analysis import ActiveAnalysisService, AnalysisActivationError
from app.modules.search.governance_model import (
    ActiveAnalysisAuditModel, SearchIndexRecordModel,
    SearchShadowObservationModel, TenantSearchShadowPolicyModel,
)
from app.modules.search.index_lifecycle import SearchIndexLifecycleService, VerificationSpec
from app.modules.search.index_types import AliasSwitchResult
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV2Index
from app.modules.search.shadow import SearchShadowComparator


class FakeIndexAdmin:
    def __init__(self):
        self.aliases = {"read": {"cam-v2-old"}, "write": {"cam-v2-old"}}
        self.deleted = []
        definition = ElasticsearchV2Index.index_definition()
        self.mapping = {"cam-v2-new": {"mappings": definition["mappings"]}}
        self.settings = {"cam-v2-new": {"settings": {"index": definition["settings"]}}}
        self.count = 3
        self.alias_checks = 0

    async def alias_indices(self):
        self.alias_checks += 1
        return {key: set(value) for key, value in self.aliases.items()}

    async def index_settings(self, _name):
        return self.settings

    async def index_count(self, _name):
        return self.count

    async def index_mapping(self, _name):
        return self.mapping

    async def verification_search(self, _name, _body):
        return {"hits": {"hits": [{"_source": {"tenant_id": "tenant-a"}}]}}

    async def switch_aliases(self, target):
        previous = tuple(self.aliases["read"])
        self.aliases = {"read": {target}, "write": {target}}
        return AliasSwitchResult(target, previous, previous)

    async def delete_index(self, name):
        self.deleted.append(name)


class SearchGovernanceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.sessions()
        assets = AssetRegistryRepository(self.session)
        metadata = AiMetadataRepository(self.session)
        self.asset = assets.create_asset(tenant_id="tenant-a", content_hash="a" * 64)
        self.profile = metadata.create_profile(
            tenant_id="tenant-a", profile_name="default", profile_version="1",
            prompt_template="Analyze",
        )
        self.analyses = []
        for suffix in ("1", "2"):
            analysis = metadata.create_analysis(
                tenant_id="tenant-a", asset_id=self.asset.id,
                metadata_profile_id=self.profile.id, prompt_version=suffix,
                pipeline_version="1", force=True,
            )
            analysis.status = "completed"
            analysis.metadata_json = {"subject": "cat"}
            analysis.search_projection = {"search_text": "cat"}
            analysis.search_projection_version = "v2"
            analysis.completed_at = datetime.now(timezone.utc)
            self.analyses.append(analysis)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    async def test_activation_is_tenant_scoped_audited_and_rolls_back(self):
        service = ActiveAnalysisService(self.session)
        first = service.activate(
            tenant_id="tenant-a", asset_id=self.asset.id,
            analysis_id=self.analyses[0].id, actor_id="admin",
        )
        second = service.activate(
            tenant_id="tenant-a", asset_id=self.asset.id,
            analysis_id=self.analyses[1].id, actor_id="admin",
        )
        self.assertEqual(first.active.id, second.active.id)
        rolled = service.rollback(
            tenant_id="tenant-a", asset_id=self.asset.id,
            metadata_profile_id=self.profile.id, actor_id="admin",
        )
        self.assertEqual(rolled.active.analysis_id, self.analyses[0].id)
        self.assertEqual(len(list(self.session.scalars(select(ActiveAnalysisAuditModel)))), 3)
        with self.assertRaises(LookupError):
            service.activate(
                tenant_id="tenant-b", asset_id=self.asset.id,
                analysis_id=self.analyses[0].id, actor_id="admin",
            )

    async def test_shadow_timeout_never_delays_primary_and_persists_bounded_data(self):
        with self.sessions() as session:
            session.add(TenantSearchShadowPolicyModel(
                tenant_id="tenant-a", enabled=True, sample_percentage=100,
                timeout_ms=10, primary_version="v1", shadow_version="v2",
                persist_raw_query=True,
            ))
            session.commit()
        comparator = SearchShadowComparator(
            session_factory=self.sessions, global_enabled=lambda: True,
            max_timeout_ms=50,
        )

        async def primary():
            return {"items": [{"id": "a"}], "total": 1}

        async def shadow():
            await asyncio.sleep(0.1)
            return {"items": [{"id": "b"}], "total": 1}

        started = time.perf_counter()
        result = await comparator.execute(
            tenant_id="tenant-a", query="cat mama", primary=primary, shadow=shadow,
        )
        self.assertEqual(result["total"], 1)
        self.assertLess(time.perf_counter() - started, 0.05)
        await comparator.drain()
        with self.sessions() as session:
            row = session.scalar(select(SearchShadowObservationModel))
            self.assertEqual(row.error_category, "timeout")
            self.assertEqual(row.query_type, "soft_and")
            self.assertEqual(row.raw_query, "cat mama")
            self.assertEqual(len(row.query_hash), 64)

    async def test_global_shadow_disable_is_an_upper_bound(self):
        with self.sessions() as session:
            session.add(TenantSearchShadowPolicyModel(
                tenant_id="tenant-a", enabled=True, sample_percentage=100,
                primary_version="v1", shadow_version="v2",
            ))
            session.commit()
        called = False

        async def shadow():
            nonlocal called
            called = True
            return {}

        comparator = SearchShadowComparator(
            session_factory=self.sessions, global_enabled=lambda: False,
            max_timeout_ms=50,
        )
        await comparator.execute(
            tenant_id="tenant-a", query="cat",
            primary=lambda: asyncio.sleep(0, result={"items": []}), shadow=shadow,
        )
        await comparator.drain()
        self.assertFalse(called)

    async def test_verify_before_activate_and_alias_safe_cleanup(self):
        provider = FakeIndexAdmin()
        lifecycle = SearchIndexLifecycleService(self.session, provider)
        new = lifecycle.register(
            physical_index_name="cam-v2-new", index_prefix="cam",
            index_version="new", projection_version="v2",
        )
        old = lifecycle.register(
            physical_index_name="cam-v2-old", index_prefix="cam",
            index_version="old", projection_version="v1",
        )
        old.lifecycle_state = "active"
        old.activated_at = datetime.now(timezone.utc) - timedelta(days=10)
        await lifecycle.verify(
            new.id, VerificationSpec("v2", minimum_document_count=1),
            actor_id="admin",
        )
        await lifecycle.activate(new.id, actor_id="admin")
        self.assertEqual(new.lifecycle_state, "active")
        self.assertEqual(old.lifecycle_state, "previous")
        old.retired_at = datetime.now(timezone.utc) - timedelta(days=3)
        dry = await lifecycle.cleanup(
            index_prefix="cam", actor_id="admin", min_age=timedelta(hours=1),
            preserve_previous=1, dry_run=True,
        )
        self.assertEqual(dry, [])
        third = lifecycle.register(
            physical_index_name="cam-v2-retired", index_prefix="cam",
            index_version="retired", projection_version="v0",
        )
        third.lifecycle_state = "retired"
        third.retired_at = datetime.now(timezone.utc) - timedelta(days=3)
        deleted = await lifecycle.cleanup(
            index_prefix="cam", actor_id="admin", min_age=timedelta(hours=1),
            preserve_previous=1, dry_run=False, confirmed=True,
        )
        self.assertEqual(deleted, ["cam-v2-retired"])
        self.assertEqual(provider.deleted, ["cam-v2-retired"])
        self.assertGreaterEqual(provider.alias_checks, 4)
