from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.authorization.folder_scope import FolderScopeResolver
from app.modules.authorization.folder_scope_cache import viewer_folder_hierarchy_cache
from app.modules.authorization.principal import (
    CurrentPrincipal,
    require_authenticated_principal,
)
from app.modules.authorization.seed import PERMISSION_DEFINITIONS, SYSTEM_ROLE_DEFINITIONS
from app.modules.public_review.board_router import router
from app.modules.public_review.board_schema import BoardIssueFilters, BoardIssueStatus
from app.modules.public_review.board_service import BoardService
from app.modules.public_review.model import (
    AssetAnnotationModel,
    PublicShareGuestModel,
    PublicShareModel,
    PublicShareScopeModel,
)


UTC = timezone.utc
BASE = datetime(2026, 9, 10, 12, tzinfo=UTC)


def principal(tenant: str = "tenant-a", *permissions: str) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=f"user-{tenant}",
        active_tenant_id=tenant,
        membership_id=f"membership-{tenant}",
        external_identity=None,
        effective_roles=frozenset(),
        effective_permissions=frozenset(permissions),
        platform_admin=False,
        session_id="clearly-fake-internal-session",
        authorization_source="test",
    )


@pytest.fixture()
def context():
    viewer_folder_hierarchy_cache.clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON")
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"),
                TenantModel(id="tenant-b", name="Tenant B", slug="tenant-b"),
            ]
        )
        db.flush()
        db.add_all(
            [
                OAuthConnectionModel(
                    id="conn-a",
                    tenant_id="tenant-a",
                    provider="google",
                    provider_account_id="account-a",
                    key_version="v1",
                ),
                OAuthConnectionModel(
                    id="conn-b",
                    tenant_id="tenant-b",
                    provider="google",
                    provider_account_id="account-b",
                    key_version="v1",
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                ExternalSourceModel(
                    id="source-a",
                    tenant_id="tenant-a",
                    source_key="source-a",
                    source_type="google_drive",
                    oauth_connection_id="conn-a",
                ),
                ExternalSourceModel(
                    id="source-a2",
                    tenant_id="tenant-a",
                    source_key="source-a2",
                    source_type="google_drive",
                    oauth_connection_id="conn-a",
                ),
                ExternalSourceModel(
                    id="source-b",
                    tenant_id="tenant-b",
                    source_key="source-b",
                    source_type="google_drive",
                    oauth_connection_id="conn-b",
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                AssetModel(
                    id="asset-shared", tenant_id="tenant-a", content_hash="a" * 64
                ),
                AssetModel(
                    id="asset-other", tenant_id="tenant-a", content_hash="c" * 64
                ),
                AssetModel(
                    id="asset-foreign", tenant_id="tenant-b", content_hash="b" * 64
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                SourceAssetModel(
                    id="folder-a",
                    tenant_id="tenant-a",
                    external_source_id="source-a",
                    external_asset_id="root-a",
                    filename="Root A",
                    source_metadata={},
                ),
                SourceAssetModel(
                    id="source-asset-a",
                    tenant_id="tenant-a",
                    external_source_id="source-a",
                    external_asset_id="file-a",
                    filename="source-a.png",
                    mime_type="image/png",
                    source_metadata={"parents": ["root-a"]},
                ),
                SourceAssetModel(
                    id="folder-a2",
                    tenant_id="tenant-a",
                    external_source_id="source-a2",
                    external_asset_id="root-a2",
                    filename="Root A2",
                    source_metadata={},
                ),
                SourceAssetModel(
                    id="source-asset-a2",
                    tenant_id="tenant-a",
                    external_source_id="source-a2",
                    external_asset_id="file-a2",
                    filename="source-a2.jpg",
                    mime_type="image/jpeg",
                    source_metadata={"parents": ["root-a2"]},
                ),
                SourceAssetModel(
                    id="source-asset-other",
                    tenant_id="tenant-a",
                    external_source_id="source-a",
                    external_asset_id="file-other",
                    filename="other.png",
                    mime_type="image/png",
                    source_metadata={"parents": ["root-a"]},
                ),
                SourceAssetModel(
                    id="source-asset-foreign",
                    tenant_id="tenant-b",
                    external_source_id="source-b",
                    external_asset_id="file-b",
                    filename="foreign.png",
                    mime_type="image/png",
                    source_metadata={"parents": ["root-b"]},
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                AssetSourceLinkModel(
                    id="link-a",
                    tenant_id="tenant-a",
                    asset_id="asset-shared",
                    source_asset_id="source-asset-a",
                ),
                AssetSourceLinkModel(
                    id="link-a2",
                    tenant_id="tenant-a",
                    asset_id="asset-shared",
                    source_asset_id="source-asset-a2",
                ),
                AssetSourceLinkModel(
                    id="link-other",
                    tenant_id="tenant-a",
                    asset_id="asset-other",
                    source_asset_id="source-asset-other",
                ),
                AssetSourceLinkModel(
                    id="link-b",
                    tenant_id="tenant-b",
                    asset_id="asset-foreign",
                    source_asset_id="source-asset-foreign",
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                PublicShareModel(
                    id="share-a",
                    public_id="public-a",
                    tenant_id="tenant-a",
                    name="Review A",
                    secret_digest="a" * 64,
                    created_by="owner-a",
                ),
                PublicShareModel(
                    id="share-a2",
                    public_id="public-a2",
                    tenant_id="tenant-a",
                    name="Review A2",
                    secret_digest="c" * 64,
                    created_by="owner-a",
                ),
                PublicShareModel(
                    id="share-b",
                    public_id="public-b",
                    tenant_id="tenant-b",
                    name="Review B",
                    secret_digest="b" * 64,
                    created_by="owner-b",
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                PublicShareScopeModel(
                    id="scope-a",
                    tenant_id="tenant-a",
                    share_id="share-a",
                    external_source_id="source-a",
                    folder_external_id="root-a",
                ),
                PublicShareScopeModel(
                    id="scope-a2",
                    tenant_id="tenant-a",
                    share_id="share-a2",
                    external_source_id="source-a2",
                    folder_external_id="root-a2",
                ),
                PublicShareScopeModel(
                    id="scope-b",
                    tenant_id="tenant-b",
                    share_id="share-b",
                    external_source_id="source-b",
                    folder_external_id="root-b",
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                PublicShareGuestModel(
                    id="guest-alice",
                    tenant_id="tenant-a",
                    share_id="share-a",
                    display_name="Guest Alice",
                ),
                PublicShareGuestModel(
                    id="guest-bob",
                    tenant_id="tenant-a",
                    share_id="share-a2",
                    display_name="Guest Bob",
                ),
                PublicShareGuestModel(
                    id="guest-foreign",
                    tenant_id="tenant-b",
                    share_id="share-b",
                    display_name="Guest Foreign",
                ),
            ]
        )
        db.commit()

    app = FastAPI()
    app.include_router(router)
    yield app, TestClient(app), factory, engine
    viewer_folder_hierarchy_cache.clear()
    engine.dispose()


def add_annotation(
    db: Session,
    *,
    annotation_id: str,
    tenant_id: str = "tenant-a",
    share_id: str = "share-a",
    asset_id: str = "asset-shared",
    source_asset_id: str = "source-asset-a",
    guest_id: str = "guest-alice",
    parent_annotation_id: str | None = None,
    status: str = "open",
    created_at: datetime = BASE,
    updated_at: datetime | None = None,
    resolved_at: datetime | None = None,
    resolved_by: str | None = None,
    pinned: bool = False,
    text: str | None = None,
) -> AssetAnnotationModel:
    body = text or annotation_id
    row = AssetAnnotationModel(
        id=annotation_id,
        tenant_id=tenant_id,
        share_id=share_id,
        asset_id=asset_id,
        source_asset_id=source_asset_id,
        guest_id=guest_id,
        parent_annotation_id=parent_annotation_id,
        anchor_x=0.25 if pinned else None,
        anchor_y=0.75 if pinned else None,
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": body}],
                }
            ],
        },
        plain_text=body,
        status=status,
        created_at=created_at,
        updated_at=updated_at or created_at,
        resolved_at=resolved_at,
        resolved_by=resolved_by,
    )
    db.add(row)
    return row


def seed_standard(factory):
    with factory() as db:
        add_annotation(
            db,
            annotation_id="root-open",
            created_at=BASE,
            updated_at=BASE + timedelta(days=4),
            pinned=True,
            text="Open issue " + "x" * 600,
        )
        add_annotation(
            db,
            annotation_id="root-resolved",
            status="resolved",
            created_at=BASE + timedelta(days=1),
            updated_at=BASE + timedelta(days=3),
            resolved_at=BASE + timedelta(days=5),
            resolved_by="internal-user-a",
        )
        add_annotation(
            db,
            annotation_id="root-source-a2",
            share_id="share-a2",
            source_asset_id="source-asset-a2",
            guest_id="guest-bob",
            created_at=BASE,
            updated_at=BASE + timedelta(days=2),
        )
        add_annotation(
            db,
            annotation_id="reply-good",
            parent_annotation_id="root-open",
            created_at=BASE + timedelta(hours=1),
        )
        add_annotation(
            db,
            annotation_id="reply-good-2",
            parent_annotation_id="root-open",
            created_at=BASE + timedelta(hours=1),
        )
        add_annotation(
            db,
            annotation_id="reply-wrong-pair",
            parent_annotation_id="root-open",
            source_asset_id="source-asset-a2",
            created_at=BASE + timedelta(hours=2),
        )
        add_annotation(
            db,
            annotation_id="foreign-root",
            tenant_id="tenant-b",
            share_id="share-b",
            asset_id="asset-foreign",
            source_asset_id="source-asset-foreign",
            guest_id="guest-foreign",
        )
        db.commit()


def api_request(context, current_principal, method: str, path: str, **kwargs):
    app, client, factory, _ = context
    app.dependency_overrides[require_authenticated_principal] = lambda: current_principal
    with patch("app.modules.public_review.board_router.SessionLocal", factory):
        response = client.request(method, path, **kwargs)
    app.dependency_overrides.clear()
    return response


@pytest.mark.parametrize("path", ["/issues", "/issues/root-open", "/stats"])
def test_read_permission_is_required_for_every_endpoint(context, path):
    response = api_request(
        context,
        principal("tenant-a", "public_review.manage", "public_review.resolve"),
        "GET",
        f"/api/v1/public-review/board{path}",
    )
    assert response.status_code == 403
    assert response.json()["detail"]["required_permission"] == "public_review.read"


@pytest.mark.parametrize("permission", ["public_review.manage", "public_review.resolve"])
def test_manage_or_resolve_does_not_substitute_for_read(context, permission):
    response = api_request(
        context,
        principal("tenant-a", permission),
        "GET",
        "/api/v1/public-review/board/issues",
    )
    assert response.status_code == 403


def test_read_only_principal_is_allowed_and_public_cookie_is_not(context):
    seed_standard(context[2])
    allowed = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/issues?status=all",
    )
    assert allowed.status_code == 200
    context[1].cookies.set("cam_public_review_session", "clearly-fake-public-session")
    denied = context[1].get("/api/v1/public-review/board/issues")
    assert denied.status_code == 401


def test_issue_semantics_exact_pairs_and_safe_list_dto(context):
    seed_standard(context[2])
    response = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/issues?status=all",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {item["id"] for item in body["items"]} == {
        "root-open",
        "root-resolved",
        "root-source-a2",
    }
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id["root-open"]["reply_count"] == 2
    assert by_id["root-open"]["asset"]["source_asset_id"] == "source-asset-a"
    assert by_id["root-source-a2"]["asset"]["source_asset_id"] == "source-asset-a2"
    assert "content_json" not in by_id["root-open"]
    assert len(by_id["root-open"]["annotation_preview"]) == 500
    assert by_id["root-resolved"]["resolver"] == {"actor_id": "internal-user-a"}
    assert by_id["root-open"]["anchor_x"] == 0.25
    assert by_id["root-resolved"]["anchor_x"] is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("status=open", {"root-open", "root-source-a2"}),
        ("status=resolved", {"root-resolved"}),
        ("status=all&share_id=share-a2", {"root-source-a2"}),
        ("status=all&external_source_id=source-a2", {"root-source-a2"}),
        ("status=all&folder_external_id=root-a", {"root-open", "root-resolved"}),
        ("status=all&asset_id=asset-shared", {"root-open", "root-resolved", "root-source-a2"}),
        ("status=all&reviewer=alice", {"root-open", "root-resolved"}),
        ("status=all&reviewer=%25", set()),
        ("status=all&pinned=true", {"root-open"}),
        ("status=all&pinned=false", {"root-resolved", "root-source-a2"}),
        (
            "status=all&created_from=2026-09-11T00:00:00Z",
            {"root-resolved"},
        ),
        (
            "status=all&created_to=2026-09-10T23:59:59Z",
            {"root-open", "root-source-a2"},
        ),
        (
            "status=all&created_from=2026-09-10T00:00:00Z&created_to=2026-09-10T23:59:59Z",
            {"root-open", "root-source-a2"},
        ),
    ],
)
def test_server_side_filters(context, query, expected):
    seed_standard(context[2])
    response = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        f"/api/v1/public-review/board/issues?{query}",
    )
    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == expected


@pytest.mark.parametrize(
    "query",
    [
        "status=invalid",
        "created_from=2026-09-10T00:00:00",
        "sort=invalid",
        "page=0",
        "page_size=0",
        "page_size=101",
        "created_from=2026-09-12T00:00:00Z&created_to=2026-09-11T00:00:00Z",
    ],
)
def test_invalid_filters_are_rejected(context, query):
    response = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        f"/api/v1/public-review/board/issues?{query}",
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("newest", ["root-resolved", "root-source-a2", "root-open"]),
        ("oldest", ["root-open", "root-source-a2", "root-resolved"]),
        ("recently_updated", ["root-open", "root-resolved", "root-source-a2"]),
        ("recently_resolved", ["root-resolved", "root-source-a2", "root-open"]),
    ],
)
def test_sorts_are_deterministic(context, sort, expected):
    seed_standard(context[2])
    response = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        f"/api/v1/public-review/board/issues?status=all&sort={sort}",
    )
    assert [item["id"] for item in response.json()["items"]] == expected


def test_pagination_is_bounded_and_stable(context):
    seed_standard(context[2])
    first = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/issues?status=all&sort=oldest&page=1&page_size=1",
    ).json()
    second = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/issues?status=all&sort=oldest&page=2&page_size=1",
    ).json()
    empty = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/issues?status=all&page=99&page_size=100",
    ).json()
    assert first["items"][0]["id"] == "root-open"
    assert second["items"][0]["id"] == "root-source-a2"
    assert first["total"] == second["total"] == empty["total"] == 3
    assert empty["items"] == []


@pytest.mark.parametrize("annotation_id", ["missing", "foreign-root", "reply-good"])
def test_detail_uses_generic_not_found_for_inaccessible_ids(context, annotation_id):
    seed_standard(context[2])
    response = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        f"/api/v1/public-review/board/issues/{annotation_id}",
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "review_issue_not_found"


def test_detail_contains_content_and_isolates_replies_by_exact_thread(context):
    seed_standard(context[2])
    response = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/issues/root-open",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content_json"]["type"] == "doc"
    assert [reply["id"] for reply in body["replies"]] == [
        "reply-good",
        "reply-good-2",
    ]
    assert all(reply["reviewer"]["display_name"] == "Guest Alice" for reply in body["replies"])


def test_tenant_isolation_for_list_detail_and_stats(context):
    seed_standard(context[2])
    list_body = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/issues?status=all",
    ).json()
    stats = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/stats",
    ).json()
    detail = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/issues/foreign-root",
    )
    assert "foreign-root" not in {item["id"] for item in list_body["items"]}
    assert stats["total_issues"] == 3
    assert detail.status_code == 404


def test_stats_zero_and_ten_with_seven_resolved(context):
    zero = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/stats",
    ).json()
    assert zero == {
        "total_issues": 0,
        "open_issues": 0,
        "resolved_issues": 0,
        "resolution_rate": 0,
        "assets_with_open_issues": 0,
        "shares_with_open_issues": 0,
    }
    with context[2]() as db:
        for index in range(10):
            resolved = index < 7
            add_annotation(
                db,
                annotation_id=f"stat-{index}",
                share_id="share-a" if index % 2 else "share-a2",
                source_asset_id=(
                    "source-asset-a" if index % 2 else "source-asset-a2"
                ),
                guest_id="guest-alice" if index % 2 else "guest-bob",
                status="resolved" if resolved else "open",
                resolved_at=BASE if resolved else None,
                resolved_by="actor" if resolved else None,
                created_at=BASE + timedelta(minutes=index),
            )
        add_annotation(
            db,
            annotation_id="stat-reply",
            parent_annotation_id="stat-9",
        )
        db.commit()
    stats = api_request(
        context,
        principal("tenant-a", "public_review.read"),
        "GET",
        "/api/v1/public-review/board/stats",
    ).json()
    assert stats == {
        "total_issues": 10,
        "open_issues": 3,
        "resolved_issues": 7,
        "resolution_rate": 70,
        "assets_with_open_issues": 2,
        "shares_with_open_issues": 2,
    }


def test_safe_responses_do_not_leak_internal_or_secret_fields(context):
    seed_standard(context[2])
    payloads = [
        api_request(
            context,
            principal("tenant-a", "public_review.read"),
            "GET",
            "/api/v1/public-review/board/issues?status=all",
        ).json(),
        api_request(
            context,
            principal("tenant-a", "public_review.read"),
            "GET",
            "/api/v1/public-review/board/issues/root-open",
        ).json(),
    ]
    forbidden = {
        "tenant_id",
        "guest_id",
        "session_id",
        "session_digest",
        "secret",
        "secret_digest",
        "credentials",
        "source_metadata",
        "provider_url",
        "filesystem_path",
    }

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    for payload in payloads:
        assert forbidden.isdisjoint(set(keys(payload)))
        serialized = str(payload)
        assert "guest-alice" not in serialized
        assert "a" * 64 not in serialized


def test_query_count_is_bounded_and_no_provider_or_hierarchy_call_for_normal_list(context):
    seed_standard(context[2])
    counts = []

    def count_query(*_):
        counts.append(1)

    event.listen(context[3], "before_cursor_execute", count_query)
    try:
        with context[2]() as db, patch.object(
            FolderScopeResolver,
            "parent_map",
            side_effect=AssertionError("normal board list must not load hierarchy"),
        ):
            small = BoardService(db).list_issues(
                tenant_id="tenant-a",
                filters=BoardIssueFilters(
                    status=BoardIssueStatus.ALL, page_size=1
                ),
            )
        first_count = len(counts)
        counts.clear()
        with context[2]() as db:
            large = BoardService(db).list_issues(
                tenant_id="tenant-a",
                filters=BoardIssueFilters(
                    status=BoardIssueStatus.ALL, page_size=100
                ),
            )
        large_count = len(counts)
        counts.clear()
        with context[2]() as db:
            BoardService(db).stats(tenant_id="tenant-a")
        stats_count = len(counts)
    finally:
        event.remove(context[3], "before_cursor_execute", count_query)
    assert small.total == large.total == 3
    assert first_count == large_count == 3
    assert stats_count == 1


def test_permission_catalog_and_tenant_admin_composition():
    assert {
        "public_review.read",
        "public_review.resolve",
        "public_review.manage",
    } <= set(PERMISSION_DEFINITIONS)
    tenant_admin_permissions = SYSTEM_ROLE_DEFINITIONS["tenant_admin"][2]
    assert {
        "public_review.read",
        "public_review.resolve",
        "public_review.manage",
    } <= tenant_admin_permissions



def test_custom_role_can_receive_public_review_read_permission(context):
    from app.modules.authorization.model import PermissionModel, RolePermissionModel
    from app.modules.authorization.seed import seed_tenant_rbac
    from app.modules.authorization.service import TenantAuthorizationService

    with context[2]() as db:
        seed_tenant_rbac(db, "tenant-a")
        role = TenantAuthorizationService(db).create_custom_role(
            tenant_id="tenant-a",
            role_key="review_reader",
            name="Review reader",
            permission_keys={"public_review.read"},
        )
        permission_key = db.scalar(
            select(PermissionModel.permission_key)
            .join(
                RolePermissionModel,
                RolePermissionModel.permission_id == PermissionModel.id,
            )
            .where(RolePermissionModel.role_id == role.id)
        )
        assert permission_key == "public_review.read"

