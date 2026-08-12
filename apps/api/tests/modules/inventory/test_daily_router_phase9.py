from __future__ import annotations
import unittest
from datetime import date
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.assets.model import ExternalSourceModel  # noqa: F401
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory import router as inventory_router
from app.modules.inventory.persistence_model import InventoryDailyRunEventModel

DAY = date(2030, 8, 9)

class DailyRouterPhase9Test(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(self.engine,expire_on_commit=False)
        with self.factory.begin() as session:
            session.add_all((TenantModel(id="a",name="A",slug="a"),TenantModel(id="b",name="B",slug="b")))
        self.app=FastAPI()
        self.app.include_router(inventory_router.router)
        self.client=TestClient(self.app)
        self.finalizer=CurrentPrincipal("user","a","m",None,frozenset(),frozenset({"inventory.read","inventory.finalize"}),False,"s","test")
        self.reader=CurrentPrincipal("user","a","m",None,frozenset(),frozenset({"inventory.read"}),False,"s","test")
    def request(self, principal, method, path, **kwargs):
        self.app.dependency_overrides.clear()
        for route in self.app.routes:
            if getattr(route,"path","").startswith("/api/inventory"):
                for permission_dependency in route.dependant.dependencies:
                    for authenticated_dependency in permission_dependency.dependencies:
                        self.app.dependency_overrides[authenticated_dependency.call]=lambda principal=principal: principal
        with patch("app.modules.inventory.router.SessionLocal",self.factory):
            return self.client.request(method,path,**kwargs)
    def test_finalize_requires_permission_and_force_audits_idempotently(self):
        self.assertEqual(403,self.request(self.reader,"POST",f"/api/inventory/daily-runs/{DAY}/finalize",json={}).status_code)
        blocked=self.request(self.finalizer,"POST",f"/api/inventory/daily-runs/{DAY}/finalize",json={})
        self.assertEqual(409,blocked.status_code)
        result=self.request(self.finalizer,"POST",f"/api/inventory/daily-runs/{DAY}/finalize",json={"force":True,"reason":"manual exception"})
        self.assertEqual(200,result.status_code)
        self.assertTrue(result.json()["forced"])
        repeated=self.request(self.finalizer,"POST",f"/api/inventory/daily-runs/{DAY}/finalize",json={"force":True,"reason":"ignored"})
        self.assertEqual(200,repeated.status_code)
        with self.factory() as session:
            self.assertEqual(1,session.scalar(select(func.count(InventoryDailyRunEventModel.id))))
