import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.explorer.tenant_source import TenantSourceResolver


class TenantSourceResolverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def add(self, *, source_type="google_drive", provider="google", purpose="google_drive_source",
            source_id="source-a", connection_id="connection-a", tenant="tenant-a",
            source_status="active", connection_status="active", metadata=None):
        with Session(self.engine) as session:
            connection = OAuthConnectionModel(
                id=connection_id, tenant_id=tenant, provider=provider,
                provider_account_id=f"{provider}-{connection_id}", connection_purpose=purpose,
                key_version="v1", status=connection_status,
            )
            source = ExternalSourceModel(
                id=source_id, tenant_id=tenant, source_key=source_id, source_type=source_type,
                oauth_connection_id=connection_id, status=source_status,
                source_metadata=metadata or {},
            )
            session.add_all((connection, source)); session.commit()

    async def resolve(self, tenant="tenant-a", source="source-a"):
        with Session(self.engine) as session:
            return await TenantSourceResolver(session).resolve(tenant_id=tenant, external_source_id=source)

    async def test_google_sharepoint_and_onedrive_resolve(self):
        cases = (
            ("google_drive", "google", "google_drive_source", "google-token"),
            ("sharepoint", "microsoft", "sharepoint_source", "microsoft-token"),
            ("onedrive", "microsoft", "onedrive_source", "microsoft-token"),
        )
        for index, (source_type, provider, purpose, token) in enumerate(cases):
            with self.subTest(source_type=source_type):
                self.add(source_type=source_type, provider=provider, purpose=purpose,
                    source_id=f"source-{index}", connection_id=f"connection-{index}")
                with patch("app.modules.explorer.tenant_source.google_access_token", new=AsyncMock(return_value="google-token")) as google, patch("app.modules.explorer.tenant_source.microsoft_access_token", new=AsyncMock(return_value="microsoft-token")) as microsoft:
                    result = await self.resolve(source=f"source-{index}")
                self.assertEqual((result.provider, result.connection_purpose, result.access_token), (provider, purpose, token))
                if provider == "google": google.assert_awaited_once_with(f"connection-{index}", require_drive_write_scope=False)
                else: microsoft.assert_awaited_once_with(f"connection-{index}", purpose=purpose)

    async def test_column_beats_metadata_and_null_never_falls_back(self):
        self.add(connection_id="connection-a", metadata={})
        self.add(connection_id="connection-b", source_id="source-b", metadata={"oauth_connection_id": "connection-a"})
        with patch("app.modules.explorer.tenant_source.google_access_token", new=AsyncMock(return_value="token")) as token:
            result = await self.resolve(source="source-b")
        self.assertEqual(result.oauth_connection_id, "connection-b")
        token.assert_awaited_once_with("connection-b", require_drive_write_scope=False)

        with Session(self.engine) as session:
            source = session.get(ExternalSourceModel, "source-a")
            source.oauth_connection_id = None
            source.source_metadata = {"oauth_connection_id": "connection-a"}
            session.commit()
        with patch("app.modules.explorer.tenant_source.google_access_token", new=AsyncMock()) as token:
            with self.assertRaises(HTTPException) as error:
                await self.resolve()
        self.assertEqual(error.exception.status_code, 409)
        token.assert_not_awaited()

    async def test_mismatches_and_inactive_records_reject_before_token(self):
        cases = (
            dict(source_type="onedrive", provider="microsoft", purpose="application_login"),
            dict(source_type="sharepoint", provider="microsoft", purpose="onedrive_source"),
            dict(source_type="google_drive", provider="microsoft", purpose="sharepoint_source"),
            dict(source_status="disconnected"),
            dict(source_status="reconnect_required"),
            dict(connection_status="revoked"),
        )
        for index, values in enumerate(cases):
            with self.subTest(values=values):
                self.add(source_id=f"bad-{index}", connection_id=f"bad-connection-{index}", **values)
                with patch("app.modules.explorer.tenant_source.google_access_token", new=AsyncMock()) as google, patch("app.modules.explorer.tenant_source.microsoft_access_token", new=AsyncMock()) as microsoft:
                    with self.assertRaises(HTTPException):
                        await self.resolve(source=f"bad-{index}")
                google.assert_not_awaited(); microsoft.assert_not_awaited()

    async def test_tenant_and_source_type_isolation(self):
        self.add()
        with patch("app.modules.explorer.tenant_source.google_access_token", new=AsyncMock()) as token:
            with self.assertRaises(HTTPException) as error:
                await self.resolve(tenant="tenant-b")
        self.assertEqual(error.exception.status_code, 404)
        token.assert_not_awaited()
        self.add(source_type="unsupported", source_id="unknown", connection_id="unknown-connection")
        with patch("app.modules.explorer.tenant_source.google_access_token", new=AsyncMock()) as token:
            with self.assertRaises(HTTPException) as error:
                await self.resolve(source="unknown")
        self.assertEqual(error.exception.status_code, 400)
        token.assert_not_awaited()

    async def test_google_write_scope_is_explicit(self):
        self.add()
        with patch("app.modules.explorer.tenant_source.google_access_token", new=AsyncMock(return_value="token")) as token:
            with Session(self.engine) as session:
                await TenantSourceResolver(session).google_drive(tenant_id="tenant-a", external_source_id="source-a", require_drive_write_scope=True)
        token.assert_awaited_once_with("connection-a", require_drive_write_scope=True)


if __name__ == "__main__":
    unittest.main()
