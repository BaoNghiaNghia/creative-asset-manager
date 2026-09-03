import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.application_logs.model import ApplicationLogModel
from app.modules.application_logs.repository import ApplicationLogRepository
from app.modules.application_logs.router import router
from app.modules.auth_persistence.model import TenantModel


class ApplicationLogApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
        self.session.flush()
        repo = ApplicationLogRepository(self.session)
        self.app_a, self.token_a = repo.create_application(tenant_id="tenant-a", slug="shop", display_name="Shop", payload_schema={"type": "object", "required": ["order_id"], "properties": {"order_id": {"type": "string"}}, "additionalProperties": True})
        self.app_b, self.token_b = repo.create_application(tenant_id="tenant-a", slug="worker", display_name="Worker", payload_schema=None)
        self.session.commit()
        app = FastAPI(); app.include_router(router)
        def database_override(): yield self.session
        app.dependency_overrides[get_db] = database_override
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.session.close(); self.engine.dispose()

    def headers(self, token, key=None):
        result = {"Authorization": f"Bearer {token}"}
        if key: result["Idempotency-Key"] = key
        return result

    def test_write_get_filter_and_application_isolation(self):
        created = self.client.post("/api/v1/application-logs", headers=self.headers(self.token_a), json={"level": "error", "event_type": "order.failed", "message": "Payment failed", "trace_id": "trace-1", "payload": {"order_id": "o-1", "code": "declined"}})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["application_slug"], "shop")
        self.assertEqual(created.json()["payload"]["order_id"], "o-1")
        own = self.client.get("/api/v1/application-logs?level=error&trace_id=trace-1", headers=self.headers(self.token_a))
        other = self.client.get("/api/v1/application-logs", headers=self.headers(self.token_b))
        self.assertEqual(own.status_code, 200); self.assertEqual(own.json()["total"], 1)
        self.assertEqual(other.status_code, 200); self.assertEqual(other.json()["total"], 0)
        self.assertEqual(own.json()["retention_days"], 10)

    def test_payload_schema_and_auth_are_enforced(self):
        invalid = self.client.post("/api/v1/application-logs", headers=self.headers(self.token_a), json={"event_type": "order.failed", "payload": {"wrong": True}})
        missing = self.client.get("/api/v1/application-logs")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["detail"]["code"], "payload_schema_mismatch")
        self.assertEqual(missing.status_code, 401)

    def test_idempotency_reuses_log(self):
        body = {"event_type": "worker.started", "payload": {}}
        first = self.client.post("/api/v1/application-logs", headers=self.headers(self.token_b, "evt-1"), json=body)
        second = self.client.post("/api/v1/application-logs", headers=self.headers(self.token_b, "evt-1"), json=body)
        self.assertEqual(first.status_code, 201); self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ApplicationLogModel)), 1)
        conflict = self.client.post("/api/v1/application-logs", headers=self.headers(self.token_b, "evt-1"), json={"event_type": "worker.failed", "payload": {}})
        self.assertEqual(conflict.status_code, 409)

    def test_expired_logs_are_hidden_and_physically_purged(self):
        now = datetime.now(timezone.utc)
        row, _ = ApplicationLogRepository(self.session).create_log(application=self.app_b, idempotency_key=None, request_hash="0" * 64, level="info", event_type="old", message=None, trace_id=None, payload={}, occurred_at=now - timedelta(days=11), now=now - timedelta(days=11))
        self.session.commit(); old_id = row.id
        response = self.client.get("/api/v1/application-logs", headers=self.headers(self.token_b))
        self.assertEqual(response.status_code, 200); self.assertEqual(response.json()["total"], 0)
        self.assertIsNone(self.session.get(ApplicationLogModel, old_id))


if __name__ == "__main__": unittest.main()
