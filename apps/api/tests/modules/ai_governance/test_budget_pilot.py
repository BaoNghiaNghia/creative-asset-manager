import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_governance.model import (
    AiBudgetAccountModel, AiBudgetEventModel, AiCostRateModel,
    AiUsageRecordModel, TenantAiBudgetPolicyModel,
)
from app.modules.ai_governance.pilot import PilotSelection, PilotService
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_governance.service import AiBudgetService
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel


class AiBudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.NamedTemporaryFile(suffix=".db")
        self.engine=create_engine(f"sqlite:///{self.tmp.name}",connect_args={"check_same_thread":False})
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(self.engine,class_=Session,expire_on_commit=False)
        self.settings=Settings()

    def tearDown(self): self.engine.dispose(); self.tmp.close()

    def _policy(self, tenant="tenant-a", daily=100, monthly=1000, run=None):
        with self.factory() as session:
            session.add(TenantAiBudgetPolicyModel(
                tenant_id=tenant,enabled=True,daily_limit_micros=daily,
                monthly_limit_micros=monthly,per_run_limit_micros=run,
                warning_threshold_percent=80,hard_stop_threshold_percent=100,
            ));session.commit()

    def test_usage_idempotency_and_cost_version_resolution(self):
        now=datetime.now(timezone.utc)
        with self.factory() as session:
            repo=AiGovernanceRepository(session)
            session.add_all([
                AiCostRateModel(provider="gemini",model="m",effective_at=now-timedelta(days=2),
                    input_unit_cost=.001,output_unit_cost=.002,media_unit_cost=.1,currency="USD"),
                AiCostRateModel(provider="gemini",model="m",effective_at=now-timedelta(days=1),
                    input_unit_cost=.002,output_unit_cost=.003,media_unit_cost=.2,currency="USD"),
            ]);session.flush()
            rate=repo.resolve_cost_rate("gemini","m",now)
            self.assertEqual(repo.estimate_cost(rate,10,0,0),20000)
            values={"provider":"gemini","model":"m","input_units":1,"output_units":2,
                "media_units":1,"locally_estimated_cost_micros":3,"currency":"USD",
                "latency_ms":4,"outcome":"completed","retry_count":0}
            first=repo.record_usage(tenant_id="tenant-a",operation_key="op",values=values)
            second=repo.record_usage(tenant_id="tenant-a",operation_key="op",values={**values,"latency_ms":5})
            self.assertEqual(first.id,second.id)
            self.assertEqual(session.scalar(select(func.count()).select_from(AiUsageRecordModel)),1)

    def test_daily_monthly_warning_and_reconciliation(self):
        self._policy(daily=100,monthly=200)
        with self.factory() as session:
            repo=AiGovernanceRepository(session); service=AiBudgetService(repo,self.settings)
            decision=service.reserve(tenant_id="tenant-a",operation_key="op",estimated_cost_micros=80)
            self.assertTrue(decision.allowed)
            service.reconcile(decision.reservation_id,60);session.commit()
            accounts=session.scalars(select(AiBudgetAccountModel)).all()
            self.assertTrue(all(value.reserved_micros==0 and value.actual_micros==60 for value in accounts))
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(AiBudgetEventModel)),1)
        with self.factory() as session:
            decision=AiBudgetService(AiGovernanceRepository(session),self.settings).reserve(
                tenant_id="tenant-a",operation_key="denied",estimated_cost_micros=50)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.code,"daily_budget_exceeded")

    def test_global_emergency_stop_overrides_policy(self):
        self._policy(daily=1000)
        with self.factory() as session:
            decision=AiBudgetService(AiGovernanceRepository(session),
                self.settings.model_copy(update={"AI_EMERGENCY_STOP_ENABLED":True})).reserve(
                    tenant_id="tenant-a",operation_key="stop",estimated_cost_micros=1)
            self.assertFalse(decision.allowed);self.assertEqual(decision.code,"global_ai_stop")

    def test_concurrent_reservations_cannot_overspend_and_are_tenant_isolated(self):
        self._policy(daily=100,monthly=None)
        self._policy(tenant="tenant-b",daily=100,monthly=None)
        barrier=threading.Barrier(2);results=[];lock=threading.Lock()
        def reserve(key):
            with self.factory() as session:
                barrier.wait()
                value=AiBudgetService(AiGovernanceRepository(session),self.settings).reserve(
                    tenant_id="tenant-a",operation_key=key,estimated_cost_micros=60)
                session.commit()
                with lock: results.append(value.allowed)
        threads=[threading.Thread(target=reserve,args=(f"op-{i}",)) for i in range(2)]
        [thread.start() for thread in threads];[thread.join() for thread in threads]
        self.assertEqual(sorted(results),[False,True])
        with self.factory() as session:
            other=AiBudgetService(AiGovernanceRepository(session),self.settings).reserve(
                tenant_id="tenant-b",operation_key="other",estimated_cost_micros=60)
            self.assertTrue(other.allowed)


class PilotServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session=Session(self.engine)
        self.settings=Settings(AI_PILOT_CONFIRMATION_THRESHOLD_MICROS=0)
        self.assets=[]
        for index in range(5):
            asset=AssetModel(tenant_id="tenant-a",content_hash=f"{index:064x}",mime_type="image/png")
            self.session.add(asset);self.assets.append(asset)
        self.session.add(AssetModel(tenant_id="tenant-b",content_hash="f"*64,mime_type="image/png"))
        self.session.add(AiCostRateModel(provider="gemini", model=self.settings.GEMINI_MODEL, processing_mode="single", effective_at=datetime.now(timezone.utc) - timedelta(days=1), input_unit_cost=0, output_unit_cost=0, media_unit_cost=0))
        self.session.flush()
        self.profile=AiMetadataRepository(self.session).create_profile(
            tenant_id="tenant-a",profile_name="general",profile_version="1",
            prompt_template="Analyze {{ asset }}")
        self.session.commit()

    def tearDown(self): self.session.close();self.engine.dispose()

    def test_deterministic_selection_cancel_resume_and_report(self):
        service=PilotService(self.session,self.settings)
        selection=PilotSelection(maximum_items=3,sample_seed="seed",golden_queries=("cat",))
        first=[value.id for value in service.select_assets("tenant-a",selection)]
        second=[value.id for value in service.select_assets("tenant-a",selection)]
        self.assertEqual(first,second);self.assertEqual(len(first),3)
        run=service.create(tenant_id="tenant-a",metadata_profile_id=self.profile.id,
            selection=selection,created_by="admin",force=True)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ProcessingJobModel)),3)
        service.cancel("tenant-a",run.id,"admin")
        self.assertEqual(run.status,"cancelled")
        service.resume("tenant-a",run.id,"admin")
        self.assertEqual(run.status,"running")
        report=service.report("tenant-a",run.id)
        self.assertEqual(report["selected_item_count"],3)
        self.assertIn("zero_result_golden_queries",report)
        self.assertIn("metric,value",service.report_csv(report))
        with self.assertRaises(LookupError): service.report("tenant-b",run.id)


class AiGovernanceAuthorizationTest(unittest.TestCase):
    def test_unauthenticated_and_cross_tenant_updates_fail(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin
        client=TestClient(app)
        response=client.patch("/api/v1/admin/ai-governance/tenant-a/budget",json={"enabled":True})
        self.assertEqual(response.status_code,401)
        app.dependency_overrides[require_processing_admin]=lambda: ProcessingAdmin(
            actor_id="tenant-a",own_tenant_id="tenant-a",platform_admin=False)
        try:
            response=client.patch("/api/v1/admin/ai-governance/tenant-b/budget",json={"enabled":True})
            self.assertEqual(response.status_code,403)
        finally:
            app.dependency_overrides.clear()


class AiPerRunBudgetTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session=Session(self.engine)
        self.session.add(TenantAiBudgetPolicyModel(
            tenant_id="tenant-a",enabled=True,daily_limit_micros=1000,
            monthly_limit_micros=2000,per_run_limit_micros=100,
            warning_threshold_percent=80,hard_stop_threshold_percent=100,
            currency="USD",
        ))
        self.session.commit()

    def tearDown(self): self.session.close();self.engine.dispose()

    def test_per_run_limit_and_currency_mismatch(self):
        service=AiBudgetService(AiGovernanceRepository(self.session),Settings())
        first=service.reserve(tenant_id="tenant-a",operation_key="one",
            estimated_cost_micros=60,pilot_run_id="pilot")
        self.assertTrue(first.allowed)
        second=service.reserve(tenant_id="tenant-a",operation_key="two",
            estimated_cost_micros=60,pilot_run_id="pilot")
        self.assertFalse(second.allowed);self.assertEqual(second.code,"pilot_budget_exceeded")
        mismatch=service.reserve(tenant_id="tenant-a",operation_key="eur",
            estimated_cost_micros=1,currency="EUR")
        self.assertFalse(mismatch.allowed);self.assertEqual(mismatch.code,"budget_currency_mismatch")
