import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.processing.model import ProcessingJobModel
from app.modules.search.coverage_audit import SearchV3CoverageAudit
from app.operations.search_cli import parser


PROJECTION = {
    "search_text": "milo mom",
    "search_terms": ["milo mom"],
    "normalized_terms": ["milo", "mom"],
    "phrases": ["milo mom"],
    "numbers": [],
    "facets": {},
    "path_values": [],
}


class FakeV3Index:
    def __init__(self, documents=()):
        self.documents = tuple(documents)
        self.queries = []

    async def search(self, body):
        self.queries.append(body)
        tenant = body["query"]["bool"]["filter"][0]["term"]["tenant_id"]
        ids = set(body["query"]["bool"]["filter"][1]["ids"]["values"])
        hits = [
            {"_id": item["asset_id"], "_source": item}
            for item in self.documents
            if item["tenant_id"] == tenant and item["asset_id"] in ids
        ]
        return {"hits": {"hits": hits}}


class SearchV3CoverageAuditTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.assets = AssetRegistryRepository(self.session)
        self.metadata = AiMetadataRepository(self.session)
        self.profile = self.metadata.create_profile(
            tenant_id="tenant-a",
            profile_name="creative-assets",
            profile_version="v1",
            prompt_template="test",
        )
        self.now = datetime(2026, 7, 26, tzinfo=timezone.utc)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _analysis(self, *, tenant_id="tenant-a", projection=PROJECTION, version="search-projection-v1", offset=0, force=False):
        profile = self.profile
        if tenant_id != "tenant-a":
            profile = self.metadata.create_profile(
                tenant_id=tenant_id,
                profile_name="creative-assets",
                profile_version="v1",
                prompt_template="test",
            )
        asset = self.assets.create_asset(
            tenant_id=tenant_id,
            content_hash=(f"{offset + 1:x}" * 64)[:64],
        )
        analysis = self.metadata.create_analysis(
            tenant_id=tenant_id,
            asset_id=asset.id,
            metadata_profile_id=profile.id,
            prompt_version="v1",
            pipeline_version="v1",
            force=force,
        )
        self.metadata.mark_running(analysis.id)
        self.metadata.complete_analysis(
            analysis_id=analysis.id,
            metadata={"subject": "Milo"},
            search_projection=projection,
            search_projection_version=version,
            projection_checksum="a" * 64 if projection is not None else None,
        )
        analysis.created_at = self.now + timedelta(seconds=offset)
        analysis.completed_at = analysis.created_at
        self.session.flush()
        return asset, analysis

    def _index_job(self, analysis, status="completed"):
        job = ProcessingJobModel(
            tenant_id=analysis.tenant_id,
            job_type="asset_index",
            entity_type="asset",
            entity_id=analysis.asset_id,
            idempotency_key=f"test:{analysis.id}:{status}",
            payload_json={"analysis_id": analysis.id},
            status=status,
        )
        self.session.add(job)
        self.session.flush()
        return job

    async def test_completed_projection_and_v3_document_is_healthy(self):
        _, analysis = self._analysis()
        self._index_job(analysis)
        self.session.commit()
        index = FakeV3Index((
            {
                "asset_id": analysis.asset_id,
                "tenant_id": "tenant-a",
                "search_projection_version": "search-projection-v1",
            },
        ))
        result = await SearchV3CoverageAudit(
            self.session,
            projection_version="search-projection-v1",
            index=index,
        ).run(tenant_id="tenant-a", page_size=10, verify_elasticsearch=True)

        self.assertEqual(result.items[0].category, "healthy")
        self.assertEqual(result.to_document()["healthy"], 1)
        self.assertEqual(index.queries[0]["query"]["bool"]["filter"][0]["term"]["tenant_id"], "tenant-a")

    async def test_missing_and_stale_projection_are_detected(self):
        _, missing = self._analysis(projection=None, offset=1)
        _, stale = self._analysis(version="search-projection-v0", offset=2)
        self.session.commit()

        result = await SearchV3CoverageAudit(
            self.session,
            projection_version="search-projection-v1",
        ).run(tenant_id="tenant-a", page_size=10)

        categories = {item.analysis_id: item.category for item in result.items}
        self.assertEqual(categories[missing.id], "projection_missing")
        self.assertEqual(categories[stale.id], "projection_stale")

    async def test_completed_index_job_but_absent_document_is_detected(self):
        _, analysis = self._analysis()
        self._index_job(analysis)
        self.session.commit()

        result = await SearchV3CoverageAudit(
            self.session,
            projection_version="search-projection-v1",
            index=FakeV3Index(),
        ).run(tenant_id="tenant-a", page_size=10, verify_elasticsearch=True)

        self.assertEqual(result.items[0].category, "database_indexed_document_missing")
        self.assertEqual(result.to_document()["document_missing"], 1)

    async def test_tenant_isolation_and_deterministic_pagination(self):
        _, first = self._analysis(offset=1)
        _, second = self._analysis(offset=2)
        _, third = self._analysis(offset=3)
        _, foreign = self._analysis(tenant_id="tenant-b", offset=4)
        for analysis in (first, second, third, foreign):
            self._index_job(analysis)
        self.session.commit()

        audit = SearchV3CoverageAudit(self.session, projection_version="search-projection-v1")
        page_one = await audit.run(tenant_id="tenant-a", page_size=2)
        page_two = await audit.run(
            tenant_id="tenant-a",
            page_size=2,
            after_created_at=page_one.next_created_at,
            after_analysis_id=page_one.next_analysis_id,
        )

        self.assertEqual([item.analysis_id for item in page_one.items], [first.id, second.id])
        self.assertEqual([item.analysis_id for item in page_two.items], [third.id])
        self.assertNotIn(foreign.id, [item.analysis_id for item in (*page_one.items, *page_two.items)])

    async def test_audit_performs_no_writes(self):
        _, analysis = self._analysis()
        self._index_job(analysis)
        self.session.commit()
        statements = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def capture(_, __, statement, ___, ____, _____):
            statements.append(statement.lower())

        try:
            await SearchV3CoverageAudit(
                self.session,
                projection_version="search-projection-v1",
            ).run(tenant_id="tenant-a", page_size=10)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertFalse(any(statement.lstrip().startswith(("insert", "update", "delete")) for statement in statements))


class SearchCoverageCliTest(unittest.TestCase):
    def test_audit_coverage_arguments_parse(self):
        args = parser().parse_args([
            "search:audit-coverage",
            "--tenant-id", "tenant-a",
            "--projection-version", "search-projection-v1",
            "--page-size", "25",
            "--limit", "100",
            "--after-created-at", "2026-07-26T00:00:00+00:00",
            "--after-analysis-id", "analysis-1",
            "--verify-elasticsearch",
            "--output-json",
        ])
        self.assertEqual(args.command, "search:audit-coverage")
        self.assertTrue(args.verify_elasticsearch)
        self.assertTrue(args.output_json)


if __name__ == "__main__":
    unittest.main()

class SearchV3CoverageRepairTest(SearchV3CoverageAuditTest):
    async def test_dry_run_creates_no_jobs_and_apply_creates_projection_first(self):
        from app.modules.search.coverage_audit import SearchV3CoverageRepair
        _, analysis = self._analysis(projection=None)
        self.session.commit()
        repair = SearchV3CoverageRepair(self.session, projection_version="search-projection-v1")
        dry = await repair.repair(
            tenant_id="tenant-a", page_size=10, repair_projections=True,
        )
        self.assertEqual(dry.projection_jobs_created, 0)
        self.assertEqual(self.session.query(ProcessingJobModel).count(), 0)

        applied = await repair.repair(
            tenant_id="tenant-a", page_size=10, apply=True, repair_projections=True,
        )
        self.session.commit()
        self.assertEqual(applied.projection_jobs_created, 1)
        job = self.session.query(ProcessingJobModel).one()
        self.assertEqual(job.job_type, "search_projection_build")
        self.assertEqual(job.entity_id, analysis.asset_id)

    async def test_document_missing_creates_only_index_and_rerun_is_idempotent(self):
        from app.modules.search.coverage_audit import SearchV3CoverageRepair
        _, analysis = self._analysis()
        self._index_job(analysis)
        self.session.commit()
        repair = SearchV3CoverageRepair(
            self.session, projection_version="search-projection-v1", index=FakeV3Index(),
        )
        first = await repair.repair(
            tenant_id="tenant-a", page_size=10, verify_elasticsearch=True,
            apply=True, repair_indexes=True,
        )
        self.session.commit()
        self.assertEqual(first.index_jobs_created, 1)
        self.assertEqual(first.projection_jobs_created, 0)
        second = await repair.repair(
            tenant_id="tenant-a", page_size=10, verify_elasticsearch=True,
            apply=True, repair_indexes=True,
        )
        self.assertEqual(second.index_jobs_created, 0)
        self.assertEqual(second.duplicate_jobs_skipped, 1)

    async def test_active_equivalent_job_is_not_duplicated_and_tenant_isolated(self):
        from app.modules.search.coverage_audit import SearchV3CoverageRepair
        _, analysis = self._analysis()
        active = self._index_job(analysis, status="pending")
        active.idempotency_key = f"direct:index:{analysis.id}:search-projection-v1:test"
        self.session.flush()
        _, foreign = self._analysis(tenant_id="tenant-b", offset=1)
        self.session.commit()
        result = await SearchV3CoverageRepair(
            self.session, projection_version="search-projection-v1",
        ).repair(tenant_id="tenant-a", page_size=10, apply=True, repair_indexes=True)
        self.assertEqual(result.duplicate_jobs_skipped, 1)
        self.assertEqual(self.session.query(ProcessingJobModel).filter_by(tenant_id="tenant-b").count(), 0)
        self.assertNotEqual(foreign.tenant_id, "tenant-a")
