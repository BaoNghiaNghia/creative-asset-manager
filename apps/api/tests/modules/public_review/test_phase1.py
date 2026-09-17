from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from alembic.config import Config
from alembic.script import ScriptDirectory
from pathlib import Path

from app.core.database import Base
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.public_review.model import PublicShareModel
from app.modules.public_review.repository import PublicReviewRepository
from app.modules.public_review.service import PublicReviewService, sha256_digest


NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"),
            TenantModel(id="tenant-b", name="Tenant B", slug="tenant-b"),
        ])
        db.flush()
        db.add_all([
            OAuthConnectionModel(id="conn-a", tenant_id="tenant-a", provider="google", provider_account_id="a", key_version="v1"),
            OAuthConnectionModel(id="conn-b", tenant_id="tenant-b", provider="google", provider_account_id="b", key_version="v1"),
        ])
        db.flush()
        db.add_all([
            ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="source-a", source_type="google_drive", oauth_connection_id="conn-a"),
            ExternalSourceModel(id="source-b", tenant_id="tenant-b", source_key="source-b", source_type="google_drive", oauth_connection_id="conn-b"),
            AssetModel(id="asset-a", tenant_id="tenant-a", content_hash="a" * 64),
            AssetModel(id="asset-b", tenant_id="tenant-b", content_hash="b" * 64),
        ])
        db.flush()
        db.add_all([
            SourceAssetModel(id="source-asset-a", tenant_id="tenant-a", external_source_id="source-a", external_asset_id="file-a"),
            SourceAssetModel(id="source-asset-b", tenant_id="tenant-b", external_source_id="source-b", external_asset_id="file-b"),
        ])
        db.flush()
        db.add_all([
            AssetSourceLinkModel(id="link-a", tenant_id="tenant-a", asset_id="asset-a", source_asset_id="source-asset-a"),
            AssetSourceLinkModel(id="link-b", tenant_id="tenant-b", asset_id="asset-b", source_asset_id="source-asset-b"),
        ])
        db.commit()
        yield db


def create_share(db, tenant="tenant-a", public_id="public-a", expires_at=None):
    return PublicReviewService(PublicReviewRepository(db), now=lambda: NOW).create_share(
        tenant_id=tenant, public_id=public_id, name="  Review   Share ", raw_secret="raw-share-secret",
        created_by="user-a", expires_at=expires_at,
    )


def test_share_secret_is_hashed_and_tenant_reads_are_isolated(session):
    share = create_share(session)
    assert share.name == "Review Share"
    assert share.secret_digest == sha256_digest("raw-share-secret")
    assert share.secret_digest != "raw-share-secret"
    assert session.scalar(select(PublicShareModel).where(PublicShareModel.secret_digest == "raw-share-secret")) is None
    repo = PublicReviewRepository(session)
    assert repo.get_share("tenant-b", share.id) is None
    assert repo.list_shares("tenant-b") == []


def test_duplicate_scope_and_foreign_source_are_rejected(session):
    share = create_share(session)
    repo = PublicReviewRepository(session)
    with pytest.raises(ValueError):
        repo.replace_scopes("tenant-a", share.id, [
            {"external_source_id": "source-a", "folder_external_id": "folder"},
            {"external_source_id": "source-a", "folder_external_id": "folder"},
        ])
    with pytest.raises(LookupError):
        repo.replace_scopes("tenant-a", share.id, [{"external_source_id": "source-b", "folder_external_id": "folder"}])


def test_session_hash_expiry_and_share_revocation(session):
    share = create_share(session, expires_at=NOW + timedelta(hours=2))
    service = PublicReviewService(PublicReviewRepository(session), now=lambda: NOW)
    with pytest.raises(ValueError):
        service.create_session(tenant_id="tenant-a", share_id=share.id, raw_session_token="raw-session", expires_at=NOW + timedelta(hours=3))
    row = service.create_session(tenant_id="tenant-a", share_id=share.id, raw_session_token="raw-session", expires_at=NOW + timedelta(hours=1))
    assert row.session_digest == sha256_digest("raw-session")
    assert row.session_digest != "raw-session"
    assert service.resolve_session(tenant_id="tenant-a", raw_session_token="raw-session").id == row.id
    PublicReviewRepository(session).revoke_share("tenant-a", share.id, NOW)
    with pytest.raises(LookupError):
        service.resolve_session(tenant_id="tenant-a", raw_session_token="raw-session")


def test_annotation_tenant_pair_anchor_and_reply_validation(session):
    share = create_share(session)
    service = PublicReviewService(PublicReviewRepository(session), now=lambda: NOW)
    guest = service.create_guest(tenant_id="tenant-a", share_id=share.id, display_name="  Ada   Lovelace ")
    assert guest.display_name == "Ada Lovelace"
    values = dict(tenant_id="tenant-a", share_id=share.id, asset_id="asset-a", source_asset_id="source-asset-a", guest_id=guest.id, content_json={"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}]}, plain_text="untrusted client text")
    root = service.create_annotation(anchor_x=.5, anchor_y=.5, **values)
    reply = service.create_annotation(anchor_x=None, anchor_y=None, parent_annotation_id=root.id, **values)
    assert root.plain_text == "hello"
    assert reply.parent_annotation_id == root.id
    with pytest.raises(ValueError):
        service.create_annotation(anchor_x=.5, anchor_y=None, **values)
    with pytest.raises(LookupError):
        service.create_annotation(anchor_x=None, anchor_y=None, **{**values, "source_asset_id": "source-asset-b"})
    other = create_share(session, public_id="public-a-2")
    other_guest = service.create_guest(tenant_id="tenant-a", share_id=other.id, display_name="Other")
    with pytest.raises(LookupError):
        service.create_annotation(anchor_x=None, anchor_y=None, **{**values, "share_id": other.id, "guest_id": other_guest.id, "parent_annotation_id": root.id})


def test_database_constraints_reject_duplicate_public_id(session):
    create_share(session)
    with pytest.raises(IntegrityError):
        create_share(session)
    session.rollback()


def test_migration_has_single_phase1_head():
    root = Path(__file__).resolve().parents[5]
    config = Config(str(root / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(root / "database/migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == ["0081_public_review_rate_limits"]

def test_public_share_requires_an_existing_tenant(session):
    session.add(PublicShareModel(
        public_id="orphan-public-share",
        tenant_id="tenant-does-not-exist",
        name="Orphan share",
        secret_digest="f" * 64,
        created_by="operator-a",
    ))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

