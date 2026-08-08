import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.bootstrap import globally_enabled_job_types
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.service import ProcessingJobService
from app.modules.processing_policy.model import ProcessingPolicyAuditModel, TenantProcessingPolicyModel
from app.modules.processing_policy.repository import ProcessingPolicyRepository
from app.modules.processing_policy.service import ProcessingPolicyService, TenantPolicyCache

NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)

class ProcessingPolicyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "policy.db"
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 10})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose(); self.directory.cleanup()

    def policy(self, tenant, *, enabled=True, paused=False, total=4, ai=1):
        with self.sessions.begin() as session:
            policy = TenantProcessingPolicyModel(
                tenant_id=tenant, pipeline_enabled=enabled, download_enabled=enabled,
                source_sync_enabled=enabled, managed_storage_enabled=enabled,
                ai_analysis_enabled=enabled, search_v2_enabled=enabled, sidecar_enabled=enabled,
                processing_paused=paused, total_active_jobs_limit=total, ai_active_jobs_limit=ai,
            )
            session.add(policy)

    def job(self, tenant, key, *, kind="asset_analyze", provider="gemini", scope="ai"):
        with self.sessions.begin() as session:
            payload = {}
            if kind == "asset_analyze":
                analysis = AssetAiAnalysisModel(
                    id=f"analysis-{tenant}-{key}", tenant_id=tenant, asset_id=f"asset-{key}",
                    content_hash=(key * 64)[:64], metadata_profile_id="profile",
                    metadata_profile="creative-assets", metadata_profile_version="v1",
                    prompt_version="prompt-v1", pipeline_version="pipeline-v1",
                    ai_provider=provider, ai_model="gemini-3.5-flash-lite",
                )
                session.add(analysis); session.flush(); payload = {"analysis_id": analysis.id}
            return ProcessingRepository(session).create_job(
                tenant_id=tenant, job_type=kind, entity_type="asset", entity_id=key,
                idempotency_key=key, payload=payload, next_attempt_at=NOW, provider_key=provider, provider_scope=scope,
            ).id

    def claim(self, worker):
        with self.sessions() as session:
            return ProcessingJobService(ProcessingRepository(session)).claim_next(
                worker_id=worker, lease_seconds=60, now=NOW, enforce_tenant_policy=True,
                allowed_job_types=("asset_analyze", "asset_store"),
            )

    def test_disabled_and_paused_tenants_are_skipped_without_starvation(self):
        self.policy("disabled", enabled=False); self.policy("paused", paused=True); self.policy("enabled")
        self.job("disabled", "a"); self.job("paused", "b"); enabled = self.job("enabled", "c")
        claimed = self.claim("worker")
        self.assertEqual(claimed.id, enabled)

    def test_resume_restores_eligibility_and_running_job_drains_during_pause(self):
        self.policy("tenant")
        first = self.job("tenant", "first"); self.job("tenant", "second")
        claimed = self.claim("worker-a"); self.assertEqual(claimed.id, first)
        with self.sessions.begin() as session:
            ProcessingPolicyRepository(session).pause_tenant("tenant", actor_id="admin", reason="maintenance")
        self.assertIsNone(self.claim("worker-b"))
        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            service.complete(job_id=first, worker_id="worker-a")
        with self.sessions.begin() as session:
            ProcessingPolicyRepository(session).resume_tenant("tenant")
        self.assertIsNotNone(self.claim("worker-b"))

    def test_expired_accounted_lease_can_be_reclaimed_at_limit(self):
        self.policy("tenant", total=1, ai=1)
        job_id = self.job("tenant", "leased")
        first = self.claim("worker-a"); self.assertEqual(first.id, job_id)
        with self.sessions() as session:
            recovered = ProcessingJobService(ProcessingRepository(session)).claim_next(
                worker_id="worker-b", lease_seconds=60, now=NOW + timedelta(seconds=61),
                enforce_tenant_policy=True, allowed_job_types=("asset_analyze",),
            )
            self.assertEqual(recovered.id, job_id)
            self.assertEqual(recovered.attempt_count, 2)

    def test_provider_pause_only_blocks_matching_jobs(self):
        self.policy("tenant")
        with self.sessions.begin() as session:
            ProcessingPolicyRepository(session).pause_provider("tenant", "gemini", "ai", actor_id="admin", reason="outage")
        self.job("tenant", "ai", provider="gemini", scope="ai")
        storage = self.job("tenant", "storage", kind="asset_store", provider="google_drive", scope="storage")
        self.assertEqual(self.claim("worker").id, storage)

    def test_concurrent_workers_respect_provider_limit(self):
        self.policy("tenant", total=4, ai=4)
        with self.sessions.begin() as session:
            ProcessingPolicyRepository(session).get_or_create_provider("tenant", "gemini", "ai")
        self.job("tenant", "provider-one"); self.job("tenant", "provider-two")
        barrier = threading.Barrier(2); claimed=[]; errors=[]
        def run(worker):
            try:
                barrier.wait(3); claimed.append(self.claim(worker))
            except Exception as exc: errors.append(exc)
        threads=[threading.Thread(target=run,args=(f"p{i}",)) for i in range(2)]
        [t.start() for t in threads]; [t.join(5) for t in threads]
        self.assertFalse(errors); self.assertEqual(sum(value is not None for value in claimed), 1)

    def test_concurrent_workers_respect_tenant_ai_limit(self):
        self.policy("tenant", total=2, ai=1)
        self.job("tenant", "one"); self.job("tenant", "two")
        barrier = threading.Barrier(2); claimed=[]; errors=[]
        def run(worker):
            try:
                barrier.wait(3); claimed.append(self.claim(worker))
            except Exception as exc: errors.append(exc)
        threads=[threading.Thread(target=run,args=(f"w{i}",)) for i in range(2)]
        [t.start() for t in threads]; [t.join(5) for t in threads]
        self.assertFalse(errors); self.assertEqual(sum(value is not None for value in claimed), 1)

    def test_policy_update_invalidates_cached_configuration(self):
        self.policy("tenant", enabled=False)
        settings = Settings(PROCESSING_JOBS_ENABLED=True, UNIFIED_ASSET_INGESTION_ENABLED=True, CONTENT_DEDUP_ENABLED=True)
        cache = TenantPolicyCache(60)
        with self.sessions.begin() as session:
            service = ProcessingPolicyService(ProcessingPolicyRepository(session), settings, cache)
            self.assertFalse(service.effective("tenant").effective["pipeline_enabled"])
            service.update("tenant", {"pipeline_enabled": True, "download_enabled": True}, actor_id="admin")
            self.assertTrue(service.effective("tenant").effective["download_enabled"])

    def test_worker_global_job_types_are_fail_closed(self):
        self.assertEqual(globally_enabled_job_types(Settings()), ())

    def test_global_disable_remains_upper_bound_even_with_cached_enable(self):
        self.policy("tenant")
        enabled = Settings(
            PROCESSING_JOBS_ENABLED=True, UNIFIED_ASSET_INGESTION_ENABLED=True,
            CONTENT_DEDUP_ENABLED=True, INCREMENTAL_SOURCE_SYNC_ENABLED=True,
            MANAGED_ASSET_STORAGE_ENABLED=True, SEARCH_PROJECTION_ENABLED=True,
            ELASTICSEARCH_V2_ENABLED=True, DRIVE_METADATA_SIDECAR_ENABLED=True,
        )
        cache=TenantPolicyCache(60)
        with self.sessions() as session:
            service=ProcessingPolicyService(ProcessingPolicyRepository(session), enabled, cache)
            self.assertTrue(service.effective("tenant").effective["download_enabled"]); session.rollback()
        enabled.CONTENT_DEDUP_ENABLED=False
        with self.sessions() as session:
            service=ProcessingPolicyService(ProcessingPolicyRepository(session), enabled, cache)
            self.assertFalse(service.effective("tenant").effective["download_enabled"])

    def test_policy_change_is_audited_and_tenant_scoped(self):
        with self.sessions.begin() as session:
            service=ProcessingPolicyService(ProcessingPolicyRepository(session), Settings())
            service.update("tenant-a", {"pipeline_enabled": True}, actor_id="admin", reason="pilot")
        with self.sessions() as session:
            audits=list(session.scalars(select(ProcessingPolicyAuditModel)))
            self.assertEqual(len(audits), 1); self.assertEqual(audits[0].tenant_id, "tenant-a")
            self.assertEqual(audits[0].actor_id, "admin")
            self.assertIsNone(ProcessingPolicyRepository(session).get_tenant("tenant-b"))

if __name__ == "__main__": unittest.main()
