import base64
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.encryption import TokenCipher, TokenEncryptionError
from app.modules.auth_persistence.model import AuthAuditEventModel, AuthSessionModel, OAuthConnectionModel, OAuthTransactionModel
from app.modules.auth_persistence.repository import AuthPersistenceRepository

def key(byte):
    return bytes([byte])*32

class EncryptionTest(unittest.TestCase):
    def test_authenticated_encryption_random_nonce_tamper_and_wrong_key(self):
        cipher=TokenCipher({"v1":key(1)},"v1")
        first=cipher.encrypt("secret",aad="record")
        second=cipher.encrypt("secret",aad="record")
        self.assertNotEqual(first.ciphertext,second.ciphertext)
        self.assertEqual(cipher.decrypt(first.ciphertext,key_version="v1",aad="record"),"secret")
        damaged=first.ciphertext[:-2]+("AA" if first.ciphertext[-2:]!="AA" else "BB")
        with self.assertRaises(TokenEncryptionError): cipher.decrypt(damaged,key_version="v1",aad="record")
        with self.assertRaises(TokenEncryptionError): TokenCipher({"v1":key(2)},"v1").decrypt(first.ciphertext,key_version="v1",aad="record")

class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(self.engine,class_=Session,expire_on_commit=False)
        self.old=TokenCipher({"v1":key(1)},"v1")
        self.rotating=TokenCipher({"v1":key(1),"v2":key(2)},"v2")

    def tearDown(self): self.engine.dispose()

    def connection(self,repository,tenant="tenant-a"):
        return repository.upsert_connection(
            tenant_id=tenant,provider="google",provider_account_id="account-a",
            connection_purpose="application_login",
            account_email="a@example.com",access_token="access-old",refresh_token="refresh-old",
            expires_at=datetime.now(timezone.utc)+timedelta(hours=1),scopes=["drive"],token_type="Bearer",
        )

    def test_sessions_are_shared_persistent_revocable_and_tenant_scoped(self):
        with self.factory() as first:
            repository=AuthPersistenceRepository(first,self.old); connection=self.connection(repository)
            session_id,_=repository.create_session(connection=connection,user={"id":"tenant-a","email":"a@example.com"},ttl_seconds=3600); first.commit()
        with self.factory() as second:
            repository=AuthPersistenceRepository(second,self.old); loaded=repository.load_session(provider="google",session_id=session_id)
            self.assertEqual(loaded.tenant_id,"tenant-a"); self.assertEqual(loaded.access_token,"access-old")
            self.assertIsNone(repository.load_session(provider="microsoft",session_id=session_id))
            self.assertTrue(repository.revoke_session(provider="google",session_id=session_id)); second.commit()
        with self.factory() as restarted:
            self.assertIsNone(AuthPersistenceRepository(restarted,self.old).load_session(provider="google",session_id=session_id))

    def test_oauth_state_is_bound_one_time_and_expires(self):
        with self.factory() as session:
            repository=AuthPersistenceRepository(session,self.old)
            repository.remember_state(provider="google",state="state-a",code_verifier="verifier",ttl_seconds=60,session_binding="browser")
            session.commit()
        with self.factory() as replica:
            repository=AuthPersistenceRepository(replica,self.old)
            with self.assertRaises(LookupError): repository.consume_state(provider="google",state="state-a",session_binding="other")
            verifier,_=repository.consume_state(provider="google",state="state-a",session_binding="browser")
            self.assertEqual(verifier,"verifier"); replica.commit()
            with self.assertRaises(LookupError): repository.consume_state(provider="google",state="state-a",session_binding="browser")
            repository.remember_state(provider="google",state="expired",code_verifier=None,ttl_seconds=1)
            replica.execute(update(OAuthTransactionModel).where(OAuthTransactionModel.state_hash.is_not(None)).values(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))); replica.commit()
            with self.assertRaises(LookupError): repository.consume_state(provider="google",state="expired")

    def test_refresh_lock_rotation_failure_and_key_rotation(self):
        with self.factory() as session:
            repository=AuthPersistenceRepository(session,self.old); connection=self.connection(repository); session.commit(); connection_id=connection.id
        with self.factory() as first:
            self.assertTrue(AuthPersistenceRepository(first,self.old).claim_refresh(tenant_id="tenant-a",connection_id=connection_id,owner="one",lease_seconds=30)); first.commit()
        with self.factory() as second:
            self.assertFalse(AuthPersistenceRepository(second,self.old).claim_refresh(tenant_id="tenant-a",connection_id=connection_id,owner="two",lease_seconds=30))
            AuthPersistenceRepository(second,self.old).finish_refresh(tenant_id="tenant-a",connection_id=connection_id,owner="one",access_token="access-new",refresh_token="refresh-new",expires_at=datetime.now(timezone.utc)+timedelta(hours=2)); second.commit()
        with self.factory() as third:
            repository=AuthPersistenceRepository(third,self.old)
            self.assertTrue(repository.claim_refresh(tenant_id="tenant-a",connection_id=connection_id,owner="three",lease_seconds=30))
            repository.fail_refresh(tenant_id="tenant-a",connection_id=connection_id,owner="three",code="invalid_grant",retryable=False); third.commit()
            row=third.get(OAuthConnectionModel,connection_id); self.assertEqual(row.status,"reconnect_required")
            row.status="active"; row.refresh_error_json=None; third.commit()
        with self.factory() as rotation:
            repository=AuthPersistenceRepository(rotation,self.rotating)
            count,cursor=repository.rotate_page(after_id=None,limit=10,dry_run=False); rotation.commit()
            self.assertEqual(count,1); self.assertTrue(cursor)
            row=rotation.get(OAuthConnectionModel,connection_id); self.assertEqual(row.key_version,"v2")
            self.assertEqual(repository.cipher.decrypt(row.refresh_token_ciphertext,key_version="v2",aad=repository._aad(row,"refresh")),"refresh-new")
            self.assertTrue(rotation.scalar(select(AuthAuditEventModel).where(AuthAuditEventModel.action=="key_rotated")))

    def test_expired_session_cleanup_and_no_plaintext_persistence(self):
        with self.factory() as session:
            repository=AuthPersistenceRepository(session,self.old); connection=self.connection(repository)
            session_id,row=repository.create_session(connection=connection,user={"id":"tenant-a"},ttl_seconds=1)
            row.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1); session.commit()
            result=repository.cleanup_expired(); session.commit()
            self.assertEqual(result["sessions"],1)
            stored=session.get(OAuthConnectionModel,connection.id)
            serialized=str({key:value for key,value in stored.__dict__.items() if not key.startswith("_")})
            self.assertNotIn("access-old",serialized); self.assertNotIn("refresh-old",serialized)


    def test_same_microsoft_account_can_hold_application_and_source_purposes(self):
        with self.factory() as session:
            repository = AuthPersistenceRepository(session, self.old)
            base = dict(
                tenant_id="tenant-a", provider="microsoft", provider_account_id="microsoft-account",
                account_email="creative@example.com", access_token="access", refresh_token="refresh",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1), scopes=["User.Read"], token_type="Bearer",
            )
            application = repository.upsert_connection(**base, connection_purpose="application_login")
            onedrive = repository.upsert_connection(**base, connection_purpose="onedrive_source")
            sharepoint = repository.upsert_connection(**base, connection_purpose="sharepoint_source")
            self.assertEqual(
                {application.connection_purpose, onedrive.connection_purpose, sharepoint.connection_purpose},
                {"application_login", "onedrive_source", "sharepoint_source"},
            )
            self.assertEqual(len({application.id, onedrive.id, sharepoint.id}), 3)

if __name__=="__main__": unittest.main()
