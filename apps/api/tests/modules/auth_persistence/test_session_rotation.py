import unittest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.modules.auth_persistence.encryption import TokenCipher
from app.modules.auth_persistence.model import AuthSessionModel, UserModel
from app.modules.auth_persistence.repository import AuthPersistenceRepository
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization import model as authorization_models  # noqa: F401

class SessionRotationTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool); Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(self.engine,class_=Session,expire_on_commit=False); self.session=self.factory(); self.repository=AuthPersistenceRepository(self.session,TokenCipher({"v1":bytes([1])*32},"v1"))
        memberships=TenantMembershipService(self.session); self.first=memberships.create_tenant(tenant_id="tenant-first",name="First",slug="first"); self.second=memberships.create_tenant(tenant_id="tenant-second",name="Second",slug="second")
        self.user=UserModel(primary_email="person@example.com",status="active"); self.session.add(self.user); self.session.flush()
        memberships.add_member(tenant_id=self.first.id,user_id=self.user.id,status="active"); memberships.add_member(tenant_id=self.second.id,user_id=self.user.id,status="active")
        self.connection=self.repository.upsert_connection(tenant_id="google-account",provider="google",provider_account_id="google-subject",connection_purpose="application_login",account_email="person@example.com",access_token="access-token",refresh_token="refresh-token",expires_at=datetime.now(timezone.utc)+timedelta(hours=1),scopes=["drive"],token_type="Bearer")
        self.raw,_=self.repository.create_session(connection=self.connection,user={"id":"google-subject"},ttl_seconds=3600,user_id=self.user.id,active_tenant_id=self.first.id); self.session.commit()
    def tearDown(self): self.session.close(); self.engine.dispose()
    def test_rotation_replaces_session_and_switches_active_tenant(self):
        replacement_id,replacement=self.repository.rotate_session_active_tenant(provider="google",session_id=self.raw,user_id=self.user.id,tenant_id=self.second.id,ttl_seconds=3600); self.session.commit()
        self.assertNotEqual(replacement_id,self.raw); self.assertEqual(replacement.active_tenant_id,self.second.id)
        self.assertIsNone(self.repository.load_session(provider="google",session_id=self.raw)); self.assertEqual(self.repository.load_session(provider="google",session_id=replacement_id).active_tenant_id,self.second.id)
    def test_invalid_tenant_preserves_current_session(self):
        with self.assertRaises(PermissionError): self.repository.rotate_session_active_tenant(provider="google",session_id=self.raw,user_id=self.user.id,tenant_id="missing",ttl_seconds=3600)
        self.session.rollback(); self.assertEqual(self.repository.load_session(provider="google",session_id=self.raw).active_tenant_id,self.first.id)
    def test_legacy_actor_session_rejected_after_cutoff(self):
        legacy_id,legacy=self.repository.create_session(connection=self.connection,user={"id":"google-subject"},ttl_seconds=3600); self.session.commit()
        self.assertIsNotNone(self.repository.load_session(provider="google",session_id=legacy_id,allow_legacy_actor_session=True))
        self.assertIsNone(self.repository.load_session(provider="google",session_id=legacy_id,allow_legacy_actor_session=False)); self.session.commit()
        self.assertIsNotNone(self.session.get(AuthSessionModel,legacy.session_id_hash).revoked_at)

    def test_active_tenant_endpoint_returns_refreshed_identity(self):
        import json
        from contextlib import contextmanager
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.core.config import Settings
        from app.modules.authorization.principal import CurrentPrincipal, ExternalIdentitySummary
        from app.modules.authorization.router import ActiveTenantRequest, select_active_tenant

        @contextmanager
        def repository_context():
            with self.factory() as database:
                repository = AuthPersistenceRepository(database, TokenCipher({"v1":bytes([1])*32},"v1"))
                try:
                    yield repository
                    database.commit()
                except Exception:
                    database.rollback()
                    raise

        principal = CurrentPrincipal(user_id=self.user.id,active_tenant_id=self.first.id,membership_id="membership",external_identity=ExternalIdentitySummary("google","google-subject","person@example.com"),effective_roles=frozenset(),effective_permissions=frozenset(),platform_admin=False,session_id="safe-hash",authorization_source="tenant_rbac")
        request = SimpleNamespace(cookies={"cam_google_session": self.raw})
        with patch("app.modules.authorization.router.auth_repository",repository_context), patch("app.modules.authorization.router.SessionLocal",self.factory), patch("app.modules.authorization.router.get_settings",return_value=Settings(AUTH_SESSION_TTL_SECONDS=3600)):
            response = select_active_tenant(ActiveTenantRequest(tenant_id=self.second.id),request,principal)
        payload = json.loads(response.body)
        self.assertEqual(payload["active_tenant_id"],self.second.id)
        self.assertNotIn("token",str(payload).lower())
        self.assertNotIn("session",str(payload).lower())
        self.assertIn("cam_google_session=",response.headers["set-cookie"])
        self.assertNotIn(self.raw,response.headers["set-cookie"])
        with self.factory() as database:
            repository=AuthPersistenceRepository(database,TokenCipher({"v1":bytes([1])*32},"v1"))
            self.assertIsNone(repository.load_session(provider="google",session_id=self.raw))

if __name__ == "__main__": unittest.main()
