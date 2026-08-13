from __future__ import annotations
import base64
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_operations import control_router
from app.modules.ai_operations.credential_model import CreativeAiCredentialAuditModel, CreativeAiCredentialModel
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository, creative_credential_cipher
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal

KEY=base64.urlsafe_b64encode(b"C"*32).decode().rstrip("=")
OLD="creative-old-key-1111"; NEW="creative-new-key-2222"
class CreativeCredentialRouterTest(unittest.TestCase):
 def setUp(self):
  self.engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool); Base.metadata.create_all(self.engine); self.sessions=sessionmaker(self.engine,expire_on_commit=False)
  with self.sessions() as s: s.add_all((TenantModel(id="tenant-a",name="A",slug="a"),TenantModel(id="tenant-b",name="B",slug="b"),OAuthConnectionModel(id="oauth-a",tenant_id="tenant-a",provider="google",provider_account_id="drive-a",key_version="v1",access_token_ciphertext="access",refresh_token_ciphertext="refresh",status="active"))); s.commit()
  self.app=FastAPI(); self.app.include_router(control_router.router); self.client=TestClient(self.app); self.settings=Settings(CREATIVE_AI_CREDENTIAL_ENCRYPTION_KEY=KEY,GEMINI_API_KEY="env-key-0000")
  self.pread=self.principal({"ai_operations.read"}); self.pmanage=self.principal({"ai_operations.read","ai_provider.configure"})
  self.patches=[patch.object(control_router,"SessionLocal",self.sessions),patch.object(control_router,"get_settings",return_value=self.settings)]
  [x.start() for x in self.patches]
 def tearDown(self): [x.stop() for x in reversed(self.patches)]; self.engine.dispose()
 @staticmethod
 def principal(perms,tenant="tenant-a"): return CurrentPrincipal("user-a",tenant,"m",None,frozenset(),frozenset(perms),False,"s","test")
 def request(self, principal, method, path, **kwargs):
  self.app.dependency_overrides.clear(); self.app.dependency_overrides[require_authenticated_principal]=lambda:principal
  return self.client.request(method,path,**kwargs)
 def store(self,secret=OLD):
  with self.sessions() as s: CreativeAiCredentialRepository(s,creative_credential_cipher(self.settings)).replace("tenant-a",secret=secret,updated_by="user-a"); s.commit()
 def test_get_is_masked_and_scope_isolated(self):
  self.store(); response=self.request(self.pread,"GET","/api/v1/admin/ai-operations/configuration/credentials/gemini")
  self.assertEqual(response.status_code,200); self.assertEqual(response.json()["masked_key"],"••••••••1111"); self.assertNotIn(OLD,response.text)
  self.assertEqual(self.request(self.principal(set()),"GET","/api/v1/admin/ai-operations/configuration/credentials/gemini").status_code,403)
 def test_put_validates_encrypts_audits_and_preserves_drive(self):
  self.store();
  with patch.object(control_router,"validate_gemini_api_key",return_value="INVALID_KEY"):
   response=self.request(self.pmanage,"PUT","/api/v1/admin/ai-operations/configuration/credentials/gemini",json={"api_key":NEW}); self.assertEqual(response.status_code,422)
  with self.sessions() as s: self.assertEqual(CreativeAiCredentialRepository(s,creative_credential_cipher(self.settings)).get_active_secret("tenant-a").secret,OLD)
  with patch.object(control_router,"validate_gemini_api_key",return_value="VALID"):
   response=self.request(self.pmanage,"PUT","/api/v1/admin/ai-operations/configuration/credentials/gemini",json={"api_key":NEW,"label":"Creative B"})
  self.assertEqual(response.status_code,200); self.assertNotIn(NEW,response.text)
  with self.sessions() as s:
   self.assertEqual(CreativeAiCredentialRepository(s,creative_credential_cipher(self.settings)).get_active_secret("tenant-a").secret,NEW); self.assertEqual(s.get(OAuthConnectionModel,"oauth-a").access_token_ciphertext,"access"); self.assertEqual(s.scalar(select(func.count(CreativeAiCredentialAuditModel.id))),2)
 def test_expected_errors_are_structured_and_do_not_leak(self):
  self.settings.CREATIVE_AI_CREDENTIAL_ENCRYPTION_KEY=""
  with patch.object(control_router,"validate_gemini_api_key",return_value="VALID"):
   response=self.request(self.pmanage,"PUT","/api/v1/admin/ai-operations/configuration/credentials/gemini",json={"api_key":NEW})
  self.assertEqual(response.status_code,503); self.assertEqual(response.json()["detail"]["code"],"creative_credential_encryption_unavailable"); self.assertNotIn(NEW,response.text)
