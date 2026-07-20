import base64
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.auth_persistence.encryption import TokenCipher
from app.modules.auth_persistence.repository import AuthPersistenceRepository
from app.operations import auth_cli

KEY1=base64.urlsafe_b64encode(b"1"*32).decode()
KEY2=base64.urlsafe_b64encode(b"2"*32).decode()

class AuthOperationsTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine); self.factory=sessionmaker(self.engine,class_=Session,expire_on_commit=False)
        with self.factory() as session:
            AuthPersistenceRepository(session,TokenCipher({"v1":b"1"*32},"v1")).upsert_connection(tenant_id="tenant-a",provider="google",provider_account_id="account-a",account_email=None,access_token="a",refresh_token="r",expires_at=datetime.now(timezone.utc)+timedelta(hours=1),scopes=[],token_type="Bearer")
            session.commit()

    def tearDown(self): self.engine.dispose()

    def test_rotation_is_dry_run_resumable_and_idempotent(self):
        settings=Settings(PERSISTENT_AUTH_ENABLED=True,OAUTH_TOKEN_ENCRYPTION_KEYS=f"v1:{KEY1},v2:{KEY2}",OAUTH_ACTIVE_KEY_VERSION="v2")
        with patch("app.operations.auth_cli.SessionLocal",self.factory),patch("app.modules.auth_persistence.service.get_settings",return_value=settings):
            dry=auth_cli.rotate_keys(page_size=1,dry_run=True,max_pages=1)
            self.assertEqual(dry["processed"],1)
            actual=auth_cli.rotate_keys(page_size=1,dry_run=False)
            self.assertEqual(actual["processed"],1)
            rerun=auth_cli.rotate_keys(page_size=1,dry_run=False)
            self.assertEqual(rerun["processed"],0)

if __name__=="__main__": unittest.main()
