from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.modules.auth_persistence.model import TenantModel
from app.modules.ai_governance.model import AiRuntimeControlModel
from app.modules.authorization.seed import (
    OPERATOR_PERMISSIONS,
    PERMISSION_DEFINITIONS,
    SYSTEM_ROLE_DEFINITIONS,
    VIEWER_PERMISSIONS,
)
from app.modules.inventory.config import InventoryWorkerConfig
from app.modules.inventory.control import InventoryControlRepository
from app.modules.inventory.health import InventoryWorkerHealth
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.registry import build_inventory_handler_registry
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.model import InventoryProcessingControlModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import (
    TenantProcessingPolicyModel,
    TenantProviderPolicyModel,
)


NOW = datetime(2030, 8, 9, 8, 0, tzinfo=timezone.utc)
PROBE_JOB_TYPE = "inventory_phase1_isolation_probe"


class InventoryIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.directory.name) / 'inventory.db'}"
        )
        TenantModel.__table__.create(self.engine)
        InventoryProcessingControlModel.__table__.create(self.engine)
        InventoryJobModel.__table__.create(self.engine)
        ProcessingJobModel.__table__.create(self.engine)
        TenantProcessingPolicyModel.__table__.create(self.engine)
        TenantProviderPolicyModel.__table__.create(self.engine)
        AiRuntimeControlModel.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _enable_inventory(self, session, *, paused: bool = False) -> None:
        InventoryControlRepository(session).configure(
            "tenant-a", enabled=True, paused=paused,
            max_active_jobs=1, max_ai_jobs=0,
        )

    def _repository(self, session) -> InventoryJobRepository:
        return InventoryJobRepository(session, (PROBE_JOB_TYPE,))

    def test_flags_and_production_registry_are_default_off(self) -> None:
        self.assertFalse(InventoryWorkerConfig.from_settings(Settings()).enabled)
        self.assertEqual(build_inventory_handler_registry().job_types, ("inventory_file_download", "inventory_document_prepare"))

    def test_permissions_are_tenant_admin_only_by_default(self) -> None:
        permissions = {"inventory.read", "inventory.control", "inventory.jobs.manage"}
        self.assertTrue(permissions <= set(PERMISSION_DEFINITIONS))
        self.assertTrue(permissions <= SYSTEM_ROLE_DEFINITIONS["tenant_admin"][2])
        self.assertTrue(permissions.isdisjoint(VIEWER_PERMISSIONS))
        self.assertTrue(permissions.isdisjoint(OPERATOR_PERMISSIONS))

    def test_inventory_job_is_never_claimed_by_creative_repository(self) -> None:
        with self.sessions() as session:
            self._enable_inventory(session)
            self._repository(session).create_job(
                tenant_id="tenant-a", job_type=PROBE_JOB_TYPE,
                entity_type="phase1_probe", entity_id="probe-a",
                idempotency_key="inventory:probe-a",
            )
            self.assertIsNone(ProcessingRepository(session).claim_next_job(
                worker_id="creative", lease_seconds=60, now=NOW,
            ))


    def test_inventory_backlog_does_not_change_tenant_aware_creative_claim(self) -> None:
        with self.sessions() as session:
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-a", pipeline_enabled=True,
                managed_storage_enabled=True,
            ))
            session.add_all([
                InventoryJobModel(
                    tenant_id="tenant-a", job_type=PROBE_JOB_TYPE,
                    entity_type="phase1_probe", entity_id=f"probe-{index}",
                    idempotency_key=f"inventory:probe-{index}", payload_json={},
                )
                for index in range(1_000)
            ])
            ProcessingRepository(session).create_job(
                tenant_id="tenant-a", job_type="asset_store", entity_type="asset",
                entity_id="asset-a", idempotency_key="creative:asset-a",
                next_attempt_at=NOW,
            )
            claimed = ProcessingRepository(session).claim_next_job(
                worker_id="creative", lease_seconds=60, now=NOW,
                enforce_tenant_policy=True, allowed_job_types=("asset_store",),
            )
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.idempotency_key, "creative:asset-a")
    def test_creative_job_is_never_claimed_by_inventory_repository(self) -> None:
        with self.sessions() as session:
            ProcessingRepository(session).create_job(
                tenant_id="tenant-a", job_type="asset_store", entity_type="asset",
                entity_id="asset-a", idempotency_key="creative:asset-a",
                next_attempt_at=NOW,
            )
            self._enable_inventory(session)
            self.assertIsNone(self._repository(session).claim_next(
                worker_id="inventory", lease_seconds=60, now=NOW,
            ))

    def test_inventory_pause_does_not_pause_creative_queue(self) -> None:
        with self.sessions() as session:
            self._enable_inventory(session, paused=True)
            ProcessingRepository(session).create_job(
                tenant_id="tenant-a", job_type="asset_store", entity_type="asset",
                entity_id="asset-a", idempotency_key="creative:asset-a",
                next_attempt_at=NOW,
            )
            self.assertIsNotNone(ProcessingRepository(session).claim_next_job(
                worker_id="creative", lease_seconds=60, now=NOW,
            ))


    def test_creative_pause_does_not_pause_inventory_queue(self) -> None:
        with self.sessions() as session:
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-a", pipeline_enabled=True, processing_paused=True,
            ))
            self._enable_inventory(session)
            repository = self._repository(session)
            repository.create_job(
                tenant_id="tenant-a", job_type=PROBE_JOB_TYPE,
                entity_type="phase1_probe", entity_id="probe-a",
                idempotency_key="inventory:probe-a",
            )
            self.assertIsNotNone(repository.claim_next(
                worker_id="inventory", lease_seconds=60, now=NOW,
            ))
    def test_inventory_concurrency_is_queue_local(self) -> None:
        with self.sessions() as session:
            self._enable_inventory(session)
            repository = self._repository(session)
            for suffix in ("a", "b"):
                repository.create_job(
                    tenant_id="tenant-a", job_type=PROBE_JOB_TYPE,
                    entity_type="phase1_probe", entity_id=f"probe-{suffix}",
                    idempotency_key=f"inventory:probe-{suffix}",
                )
            self.assertIsNotNone(repository.claim_next(
                worker_id="inventory-a", lease_seconds=60, now=NOW,
            ))
            self.assertIsNone(repository.claim_next(
                worker_id="inventory-b", lease_seconds=60, now=NOW,
            ))
            self.assertEqual(session.scalar(select(func.count(ProcessingJobModel.id))), 0)

    def test_health_drains_fail_closed(self) -> None:
        health = InventoryWorkerHealth("inventory-a")
        health.mark_ready(True)
        self.assertTrue(health.snapshot()["ready"])
        health.start_draining()
        self.assertFalse(health.snapshot()["ready"])
        self.assertTrue(health.snapshot()["draining"])

    def test_unregistered_business_job_cannot_be_enqueued(self) -> None:
        with self.sessions() as session:
            with self.assertRaisesRegex(ValueError, "Unregistered Inventory job type"):
                InventoryJobRepository(session).create_job(
                    tenant_id="tenant-a", job_type="inventory_unimplemented",
                    entity_type="unknown", entity_id="unknown",
                    idempotency_key="inventory:unknown",
                )
