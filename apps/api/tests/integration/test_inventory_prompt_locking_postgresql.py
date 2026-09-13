"""Real PostgreSQL proof for Inventory prompt mutation row locking."""
from __future__ import annotations
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import unittest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from app.modules.auth_persistence.model import TenantModel
from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.daily_sheet.prompts import InventoryPromptResolver
from app.modules.inventory.persistence_model import InventoryPromptVersionModel, InventorySettingsModel

URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")

@unittest.skipUnless(URL.startswith(("postgresql://", "postgresql+psycopg://")), "requires disposable PostgreSQL URL")
class InventoryPromptLockingPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=create_engine(URL,pool_pre_ping=True); assert cls.engine.dialect.name == "postgresql"
        cls.sessions=sessionmaker(cls.engine,class_=Session,expire_on_commit=False)
    @classmethod
    def tearDownClass(cls): cls.engine.dispose()
    def _tenant(self):
        tenant=f"p{uuid4().hex}"
        with self.sessions.begin() as s:
            s.add(TenantModel(id=tenant,name=tenant,slug=tenant)); s.add(ExternalSourceModel(id=f"s{uuid4().hex}",tenant_id=tenant,source_key=tenant,source_type="google_drive")); s.flush(); source=s.scalar(select(ExternalSourceModel.id).where(ExternalSourceModel.tenant_id==tenant)); s.add(InventorySettingsModel(tenant_id=tenant,external_source_id=source,inbox_folder_id="inbox"))
        return tenant
    def test_concurrent_drafts_and_activations_are_serialized(self):
        tenant=self._tenant(); resolver=InventoryPromptResolver(self.sessions)
        def draft(content): return InventoryPromptResolver(self.sessions).draft(tenant,"daily_gemini_processing",content,"test")
        with ThreadPoolExecutor(max_workers=2) as pool: drafts=list(pool.map(draft,["A","B"]))
        self.assertEqual({1,2},{item["version"] for item in drafts})
        def activate(item): return InventoryPromptResolver(self.sessions).activate(tenant,"daily_gemini_processing",item["id"],"test")
        with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(activate,drafts))
        with self.sessions() as s:
            rows=list(s.scalars(select(InventoryPromptVersionModel).where(InventoryPromptVersionModel.tenant_id==tenant)))
        self.assertEqual(2,len(rows)); self.assertEqual(1,sum(row.status=="active" for row in rows)); self.assertEqual({"active","archived"},{row.status for row in rows})
    def test_missing_settings_does_not_mutate_unlocked(self):
        tenant=f"p{uuid4().hex}"
        with self.sessions.begin() as s: s.add(TenantModel(id=tenant,name=tenant,slug=tenant))
        with self.assertRaisesRegex(RuntimeError,"inventory_settings_required"):
            InventoryPromptResolver(self.sessions).draft(tenant,"daily_gemini_processing","x","test")

    def test_tenant_lock_scope_keeps_independent_versions_isolated(self):
        first, second = self._tenant(), self._tenant()
        def draft(tenant):
            return InventoryPromptResolver(self.sessions).draft(tenant, "carry_forward_0900", tenant, "test")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(draft, (first, second)))
        self.assertEqual([1, 1], sorted(item["version"] for item in results))
        with self.sessions() as s:
            counts = [
                s.scalar(select(func.count()).select_from(InventoryPromptVersionModel).where(InventoryPromptVersionModel.tenant_id == tenant))
                for tenant in (first, second)
            ]
        self.assertEqual([1, 1], counts)
