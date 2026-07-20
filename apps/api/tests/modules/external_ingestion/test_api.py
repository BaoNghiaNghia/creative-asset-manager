import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.modules.assets.model import SourceAssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.external_ingestion.model import (
    AssetIngestionItemModel,
    AssetIngestionModel,
    ExternalApiCredentialModel,
)
from app.modules.external_ingestion.repository import ExternalIngestionRepository
from app.modules.external_ingestion.router import router
from app.modules.processing.model import ProcessingJobModel


SENSITIVE_KEY = "v1:eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="
TOKEN_A = "supplier-a-token-00000000000000000001"
TOKEN_B = "supplier-b-token-00000000000000000001"


class ExternalIngestionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        assets = AssetRegistryRepository(self.session)
        source_a = assets.upsert_external_source(
            tenant_id="tenant-a", source_key="supplier-a", source_type="external_api"
        )
        source_b = assets.upsert_external_source(
            tenant_id="tenant-b", source_key="supplier-b", source_type="external_api"
        )
        credentials = ExternalIngestionRepository(self.session)
        credentials.create_credential(
            tenant_id="tenant-a",
            external_source_id=source_a.id,
            name="supplier-a",
            raw_key=TOKEN_A,
            rate_limit_per_minute=100,
        )
        credentials.create_credential(
            tenant_id="tenant-b",
            external_source_id=source_b.id,
            name="supplier-b",
            raw_key=TOKEN_B,
            rate_limit_per_minute=100,
        )
        self.session.commit()
        self.source_a = source_a.id
        self.source_b = source_b.id

        app = FastAPI()
        app.include_router(router)

        def database_override():
            yield self.session

        app.dependency_overrides[get_db] = database_override
        app.dependency_overrides[get_settings] = lambda: Settings(
            EXTERNAL_INGESTION_API_ENABLED=True,
            SENSITIVE_URL_ENCRYPTION_KEYS=SENSITIVE_KEY,
        )
        self.app = app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.session.close()
        self.engine.dispose()

    def body(self, *, source_id: str | None = None, filename: str = "cat.jpg") -> dict:
        return {
            "source_id": source_id or self.source_a,
            "items": [
                {
                    "external_asset_id": "supplier-cat-001",
                    "download_url": "https://cdn.example.com/cat.jpg?signature=secret",
                    "checksum": "sha256:abc",
                    "filename": filename,
                    "modified_at": "2026-07-19T08:30:00+07:00",
                },
                {
                    "external_asset_id": "supplier-cat-002",
                    "download_url": "https://cdn.example.com/cat-2.jpg",
                },
            ],
        }

    @staticmethod
    def headers(token: str = TOKEN_A, key: str = "supplier-page-001") -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}

    def test_authenticated_request_is_accepted_and_only_enqueues(self) -> None:
        response = self.client.post(
            "/api/v1/asset-ingestions",
            headers=self.headers(),
            json=self.body(),
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(response.json()["received"], 2)
        self.assertEqual(response.headers["x-ratelimit-limit"], "100")
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ProcessingJobModel)), 2)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(SourceAssetModel)), 0)
        jobs = list(self.session.scalars(select(ProcessingJobModel)))
        self.assertTrue(all(job.status == "pending" for job in jobs))

    def test_same_key_same_body_returns_existing_and_different_body_conflicts(self) -> None:
        first = self.client.post(
            "/api/v1/asset-ingestions", headers=self.headers(), json=self.body()
        )
        second = self.client.post(
            "/api/v1/asset-ingestions", headers=self.headers(), json=self.body()
        )
        conflict = self.client.post(
            "/api/v1/asset-ingestions",
            headers=self.headers(),
            json=self.body(filename="different.jpg"),
        )
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["ingestion_id"], second.json()["ingestion_id"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(AssetIngestionModel)), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ProcessingJobModel)), 2)

    def test_status_and_items_are_tenant_scoped(self) -> None:
        created = self.client.post(
            "/api/v1/asset-ingestions", headers=self.headers(), json=self.body()
        ).json()
        ingestion_id = created["ingestion_id"]
        status = self.client.get(
            f"/api/v1/asset-ingestions/{ingestion_id}",
            headers={"Authorization": f"Bearer {TOKEN_A}"},
        )
        items = self.client.get(
            f"/api/v1/asset-ingestions/{ingestion_id}/items",
            headers={"Authorization": f"Bearer {TOKEN_A}"},
        )
        hidden = self.client.get(
            f"/api/v1/asset-ingestions/{ingestion_id}",
            headers={"Authorization": f"Bearer {TOKEN_B}"},
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["queued"], 2)
        self.assertEqual(items.status_code, 200)
        self.assertEqual([item["status"] for item in items.json()["items"]], ["queued", "queued"])
        self.assertEqual(hidden.status_code, 404)

    def test_authentication_and_source_authorization(self) -> None:
        missing = self.client.post("/api/v1/asset-ingestions", json=self.body())
        invalid = self.client.post(
            "/api/v1/asset-ingestions",
            headers=self.headers("invalid-token-with-enough-characters-000"),
            json=self.body(),
        )
        forbidden = self.client.post(
            "/api/v1/asset-ingestions",
            headers=self.headers(),
            json=self.body(source_id=self.source_b),
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(forbidden.status_code, 403)
        credential = self.session.scalar(
            select(ExternalApiCredentialModel).where(ExternalApiCredentialModel.tenant_id == "tenant-a")
        )
        self.assertNotEqual(credential.secret_hash, TOKEN_A)
        self.assertNotIn(TOKEN_A, credential.secret_hash)

    def test_rate_limit_is_database_backed(self) -> None:
        with Session(self.engine) as session:
            source = AssetRegistryRepository(session).upsert_external_source(
                tenant_id="tenant-rate", source_key="rate", source_type="external_api"
            )
            token = "rate-limit-token-000000000000000000001"
            ExternalIngestionRepository(session).create_credential(
                tenant_id="tenant-rate",
                external_source_id=source.id,
                name="rate",
                raw_key=token,
                rate_limit_per_minute=1,
            )
            session.commit()
            source_id = source.id
        first = self.client.post(
            "/api/v1/asset-ingestions",
            headers=self.headers(token, "rate-1"),
            json=self.body(source_id=source_id),
        )
        limited = self.client.get(
            f"/api/v1/asset-ingestions/{first.json()['ingestion_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("retry-after", limited.headers)

    def test_payload_item_and_external_id_limits(self) -> None:
        oversized = json.dumps(
            {
                "source_id": self.source_a,
                "items": [
                    {
                        "external_asset_id": "large",
                        "download_url": "https://cdn.example.com/large.jpg",
                        "filename": "a" * 1_100_000,
                    }
                ],
            }
        )
        too_large = self.client.post(
            "/api/v1/asset-ingestions",
            headers={**self.headers(key="large"), "Content-Type": "application/json"},
            content=oversized,
        )
        too_many_body = {
            "source_id": self.source_a,
            "items": [
                {
                    "external_asset_id": f"asset-{index}",
                    "download_url": "https://cdn.example.com/file.jpg",
                }
                for index in range(1_001)
            ],
        }
        too_many = self.client.post(
            "/api/v1/asset-ingestions",
            headers=self.headers(key="many"),
            json=too_many_body,
        )
        invalid_id = self.client.post(
            "/api/v1/asset-ingestions",
            headers=self.headers(key="invalid-id"),
            json={
                "source_id": self.source_a,
                "items": [
                    {
                        "external_asset_id": "bad id",
                        "download_url": "https://cdn.example.com/file.jpg",
                    }
                ],
            },
        )
        self.assertEqual(too_large.status_code, 413)
        self.assertEqual(too_many.status_code, 422)
        self.assertEqual(invalid_id.status_code, 422)

    def test_feature_disabled_by_default(self) -> None:
        self.app.dependency_overrides[get_settings] = lambda: Settings()
        response = self.client.post(
            "/api/v1/asset-ingestions", headers=self.headers(), json=self.body()
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
