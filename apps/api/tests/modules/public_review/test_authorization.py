from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.authorization.folder_scope_cache import viewer_folder_hierarchy_cache
from app.modules.public_review.authorization import PublicShareAccessDenied, PublicShareScopeService
from app.modules.public_review.repository import PublicReviewRepository
from app.modules.public_review.service import PublicReviewService


NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


@pytest.fixture()
def review_context():
    viewer_folder_hierarchy_cache.clear()
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"),
            TenantModel(id="tenant-b", name="Tenant B", slug="tenant-b"),
            OAuthConnectionModel(id="conn-a", tenant_id="tenant-a", provider="google", provider_account_id="a", key_version="v1"),
            OAuthConnectionModel(id="conn-b", tenant_id="tenant-b", provider="google", provider_account_id="b", key_version="v1"),
        ])
        db.flush()
        db.add_all([
            ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="a", source_type="google_drive", oauth_connection_id="conn-a"),
            ExternalSourceModel(id="source-a-other", tenant_id="tenant-a", source_key="a-other", source_type="google_drive", oauth_connection_id="conn-a"),
            ExternalSourceModel(id="source-b", tenant_id="tenant-b", source_key="b", source_type="google_drive", oauth_connection_id="conn-b"),
            AssetModel(id="asset-allowed", tenant_id="tenant-a", content_hash="a" * 64),
            AssetModel(id="asset-sibling", tenant_id="tenant-a", content_hash="c" * 64),
            AssetModel(id="asset-b", tenant_id="tenant-b", content_hash="b" * 64),
        ])
        db.flush()
        db.add_all([
            SourceAssetModel(id="folder-root", tenant_id="tenant-a", external_source_id="source-a", external_asset_id="folder-selected", source_metadata={"parents": []}),
            SourceAssetModel(id="folder-child", tenant_id="tenant-a", external_source_id="source-a", external_asset_id="folder-child", source_metadata={"parents": ["folder-selected"]}),
            SourceAssetModel(id="source-allowed", tenant_id="tenant-a", external_source_id="source-a", external_asset_id="file-allowed", source_metadata={"parents": ["folder-child"]}),
            SourceAssetModel(id="source-sibling", tenant_id="tenant-a", external_source_id="source-a", external_asset_id="file-sibling", source_metadata={"parents": ["folder-sibling"]}),
            SourceAssetModel(id="source-other", tenant_id="tenant-a", external_source_id="source-a-other", external_asset_id="file-allowed", source_metadata={"parents": ["folder-other"]}),
            SourceAssetModel(id="source-b-asset", tenant_id="tenant-b", external_source_id="source-b", external_asset_id="file-b", source_metadata={"parents": ["folder-b"]}),
        ])
        db.flush()
        db.add_all([
            AssetSourceLinkModel(tenant_id="tenant-a", asset_id="asset-allowed", source_asset_id="source-allowed"),
            AssetSourceLinkModel(tenant_id="tenant-a", asset_id="asset-allowed", source_asset_id="source-other"),
            AssetSourceLinkModel(tenant_id="tenant-a", asset_id="asset-sibling", source_asset_id="source-sibling"),
            AssetSourceLinkModel(tenant_id="tenant-b", asset_id="asset-b", source_asset_id="source-b-asset"),
        ])
        db.flush()
        review = PublicReviewService(PublicReviewRepository(db), now=lambda: NOW)
        share = review.create_share(
            tenant_id="tenant-a", public_id="public-a", name="Public A",
            raw_secret="share-secret", created_by="operator-a",
        )
        PublicReviewRepository(db).replace_scopes(
            "tenant-a", share.id,
            [{"external_source_id": "source-a", "folder_external_id": "folder-selected"}],
        )
        session = review.create_session(
            tenant_id="tenant-a", share_id=share.id, raw_session_token="session-a",
            expires_at=NOW + timedelta(hours=1),
        )
        db.commit()
        yield db, share, session
    viewer_folder_hierarchy_cache.clear()
    engine.dispose()


def test_public_scope_allows_selected_descendants_and_exact_source_pairs(review_context):
    db, share, session = review_context
    service = PublicShareScopeService(db, now=lambda: NOW)
    principal = service.resolve_principal(
        raw_session_token="session-a", expected_public_id="public-a",
    )

    assert principal.share_id == share.id
    assert principal.session_id == session.id
    assert service.allowed_asset_source_pairs(principal=principal) == {
        ("asset-allowed", "source-allowed"),
    }
    service.authorize_asset_source_pair(
        principal=principal, asset_id="asset-allowed", source_asset_id="source-allowed",
    )
    assert service.allows_external_asset(
        principal=principal, external_source_id="source-a", external_asset_id="folder-selected",
    )
    assert service.allows_external_asset(
        principal=principal, external_source_id="source-a", external_asset_id="file-allowed",
    )


@pytest.mark.parametrize(
    ("asset_id", "source_asset_id"),
    [
        ("asset-sibling", "source-sibling"),  # sibling folder
        ("asset-allowed", "source-other"),  # canonical asset through other source
        ("asset-b", "source-b-asset"),  # another tenant
        ("file-allowed", "source-allowed"),  # provider ID masquerading as asset ID
    ],
)
def test_public_scope_denies_non_exact_or_cross_tenant_asset_pairs(
    review_context, asset_id, source_asset_id,
):
    db, _, _ = review_context
    service = PublicShareScopeService(db, now=lambda: NOW)
    principal = service.resolve_principal(raw_session_token="session-a")

    with pytest.raises(PublicShareAccessDenied) as denied:
        service.authorize_asset_source_pair(
            principal=principal, asset_id=asset_id, source_asset_id=source_asset_id,
        )
    assert denied.value.args == ()


def test_public_scope_denies_sibling_unknown_source_and_missing_hierarchy(review_context):
    db, share, _ = review_context
    service = PublicShareScopeService(db, now=lambda: NOW)
    principal = service.resolve_principal(raw_session_token="session-a")

    assert not service.allows_external_asset(
        principal=principal, external_source_id="source-a", external_asset_id="file-sibling",
    )
    assert not service.allows_external_asset(
        principal=principal, external_source_id="source-a-other", external_asset_id="file-allowed",
    )
    PublicReviewRepository(db).replace_scopes(
        "tenant-a", share.id,
        [{"external_source_id": "source-a-other", "folder_external_id": "folder-other"}],
    )
    viewer_folder_hierarchy_cache.clear()
    assert not service.allows_external_asset(
        principal=principal, external_source_id="source-a-other", external_asset_id="missing-child",
    )


@pytest.mark.parametrize(
    ("token", "public_id", "mutate"),
    [
        ("unknown", None, None),
        ("session-a", "wrong-public-id", None),
        ("session-a", None, "revoke_session"),
        ("session-a", None, "expire_session"),
        ("session-a", None, "revoke_share"),
    ],
)
def test_public_principal_denies_unknown_revoked_and_expired_sessions(
    review_context, token, public_id, mutate,
):
    db, share, session = review_context
    repo = PublicReviewRepository(db)
    if mutate == "revoke_session":
        repo.revoke_session("tenant-a", share.id, session.id, NOW)
    elif mutate == "expire_session":
        session.expires_at = NOW - timedelta(seconds=1)
        db.flush()
    elif mutate == "revoke_share":
        repo.revoke_share("tenant-a", share.id, NOW)

    service = PublicShareScopeService(db, now=lambda: NOW)
    with pytest.raises(PublicShareAccessDenied) as denied:
        service.resolve_principal(raw_session_token=token, expected_public_id=public_id)
    assert denied.value.args == ()
