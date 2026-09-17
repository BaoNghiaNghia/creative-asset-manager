from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.modules.assets.model import SourceAssetModel
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.public_review.board_repository import BoardRepository
from app.modules.public_review.board_service import BoardService
from app.modules.public_review.model import AssetAnnotationModel
from tests.modules.public_review.test_board_api import (
    BASE,
    add_annotation,
    api_request,
    context,
    principal,
    seed_standard,
)


BOARD = "/api/v1/public-review/board"


def mutate(context, actor, annotation_id: str, operation: str):
    return api_request(
        context,
        actor,
        "POST",
        f"{BOARD}/issues/{annotation_id}/{operation}",
    )


def audit_rows(factory, action: str | None = None):
    with factory() as db:
        query = select(AuthAuditEventModel).where(
            AuthAuditEventModel.tenant_id == "tenant-a"
        )
        if action:
            query = query.where(AuthAuditEventModel.action == action)
        return list(db.scalars(query.order_by(AuthAuditEventModel.occurred_at)))


@pytest.mark.parametrize("operation", ["resolve", "reopen"])
@pytest.mark.parametrize(
    "permissions",
    [
        (),
        ("public_review.read",),
        ("public_review.manage",),
        ("public_review.read", "public_review.manage"),
    ],
)
def test_mutations_require_exact_resolve_permission(
    context, operation, permissions
):
    seed_standard(context[2])
    response = mutate(
        context,
        principal("tenant-a", *permissions),
        "root-open" if operation == "resolve" else "root-resolved",
        operation,
    )
    assert response.status_code == 403
    assert (
        response.json()["detail"]["required_permission"]
        == "public_review.resolve"
    )


def test_resolve_only_permission_can_mutate_but_cannot_read(context):
    seed_standard(context[2])
    actor = principal("tenant-a", "public_review.resolve")
    resolved = mutate(context, actor, "root-open", "resolve")
    read = api_request(
        context,
        actor,
        "GET",
        f"{BOARD}/issues/root-open",
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert read.status_code == 403


def test_public_cookie_cannot_resolve_or_reopen(context):
    seed_standard(context[2])
    _, client, _, _ = context
    client.cookies.set(
        "cam_public_review_session", "clearly-fake-public-session"
    )
    for operation, target in (
        ("resolve", "root-open"),
        ("reopen", "root-resolved"),
    ):
        response = client.post(f"{BOARD}/issues/{target}/{operation}")
        assert response.status_code == 401


def test_resolve_sets_only_workflow_fields_and_audits_once(context):
    seed_standard(context[2])
    _, _, factory, _ = context
    with factory() as db:
        before = db.get(AssetAnnotationModel, "root-open")
        source = db.get(SourceAssetModel, "source-asset-a")
        immutable = {
            "content_json": deepcopy(before.content_json),
            "plain_text": before.plain_text,
            "guest_id": before.guest_id,
            "share_id": before.share_id,
            "asset_id": before.asset_id,
            "source_asset_id": before.source_asset_id,
            "anchor_x": before.anchor_x,
            "anchor_y": before.anchor_y,
            "edited_at": before.edited_at,
            "source_metadata": deepcopy(source.source_metadata),
        }

    response = mutate(
        context,
        principal("tenant-a", "public_review.resolve"),
        "root-open",
        "resolve",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["transitioned"] is True
    assert body["resolved_at"] is not None
    assert body["resolver"] == {"actor_id": "user-tenant-a"}
    assert set(body) == {
        "id",
        "status",
        "resolved_at",
        "resolver",
        "updated_at",
        "transitioned",
    }

    with factory() as db:
        after = db.get(AssetAnnotationModel, "root-open")
        source = db.get(SourceAssetModel, "source-asset-a")
        assert after.status == "resolved"
        assert after.resolved_at is not None
        assert after.resolved_by == "user-tenant-a"
        assert after.content_json == immutable["content_json"]
        assert after.plain_text == immutable["plain_text"]
        assert after.guest_id == immutable["guest_id"]
        assert after.share_id == immutable["share_id"]
        assert after.asset_id == immutable["asset_id"]
        assert after.source_asset_id == immutable["source_asset_id"]
        assert after.anchor_x == immutable["anchor_x"]
        assert after.anchor_y == immutable["anchor_y"]
        assert after.edited_at == immutable["edited_at"]
        assert source.source_metadata == immutable["source_metadata"]

    rows = audit_rows(factory, "review_issue_resolved")
    assert len(rows) == 1
    event = rows[0]
    assert event.tenant_id == "tenant-a"
    assert event.actor_id == "user-tenant-a"
    assert event.detail_json == {
        "annotation_id": "root-open",
        "share_id": "share-a",
        "asset_id": "asset-shared",
        "source_asset_id": "source-asset-a",
        "old_status": "open",
        "new_status": "resolved",
    }
    assert event.provider is None
    assert event.connection_id is None
    assert event.session_id_hash is None


def test_repeated_resolve_preserves_first_timestamp_actor_and_audit(context):
    seed_standard(context[2])
    _, _, factory, _ = context
    first = mutate(
        context,
        principal("tenant-a", "public_review.resolve"),
        "root-open",
        "resolve",
    )
    assert first.status_code == 200
    with factory() as db:
        after_first = db.get(AssetAnnotationModel, "root-open")
        first_timestamp = after_first.resolved_at
        first_updated_at = after_first.updated_at
        first_actor = after_first.resolved_by

    second = mutate(
        context,
        replace(
            principal("tenant-a", "public_review.resolve"),
            user_id="user-second-resolver",
        ),
        "root-open",
        "resolve",
    )
    assert second.status_code == 200
    assert second.json()["transitioned"] is False
    assert second.json()["resolver"] == {"actor_id": first_actor}
    with factory() as db:
        after_second = db.get(AssetAnnotationModel, "root-open")
        assert after_second.resolved_at == first_timestamp
        assert after_second.resolved_by == first_actor
        assert after_second.updated_at == first_updated_at
    assert len(audit_rows(factory, "review_issue_resolved")) == 1


def test_reopen_clears_resolution_fields_preserves_content_and_is_idempotent(context):
    seed_standard(context[2])
    _, _, factory, _ = context
    with factory() as db:
        before = db.get(AssetAnnotationModel, "root-resolved")
        immutable = {
            "content_json": deepcopy(before.content_json),
            "plain_text": before.plain_text,
            "guest_id": before.guest_id,
            "share_id": before.share_id,
            "asset_id": before.asset_id,
            "source_asset_id": before.source_asset_id,
            "anchor_x": before.anchor_x,
            "anchor_y": before.anchor_y,
            "edited_at": before.edited_at,
        }

    first = mutate(
        context,
        principal("tenant-a", "public_review.resolve"),
        "root-resolved",
        "reopen",
    )
    assert first.status_code == 200
    assert first.json()["transitioned"] is True
    assert first.json()["status"] == "open"
    assert first.json()["resolved_at"] is None
    assert first.json()["resolver"] is None

    with factory() as db:
        reopened = db.get(AssetAnnotationModel, "root-resolved")
        first_updated_at = reopened.updated_at
        assert reopened.status == "open"
        assert reopened.resolved_at is None
        assert reopened.resolved_by is None
        for field, value in immutable.items():
            assert getattr(reopened, field) == value

    second = mutate(
        context,
        replace(
            principal("tenant-a", "public_review.resolve"),
            user_id="user-second-resolver",
        ),
        "root-resolved",
        "reopen",
    )
    assert second.status_code == 200
    assert second.json()["transitioned"] is False
    with factory() as db:
        after_second = db.get(AssetAnnotationModel, "root-resolved")
        assert after_second.updated_at == first_updated_at
    assert len(audit_rows(factory, "review_issue_reopened")) == 1


@pytest.mark.parametrize("operation", ["resolve", "reopen"])
@pytest.mark.parametrize(
    "annotation_id",
    ["missing", "foreign-root", "reply-good", "malformed-pair"],
)
def test_unknown_foreign_reply_and_malformed_targets_are_generic_not_found(
    context, operation, annotation_id
):
    seed_standard(context[2])
    with context[2]() as db:
        add_annotation(
            db,
            annotation_id="malformed-pair",
            asset_id="asset-shared",
            source_asset_id="source-asset-other",
        )
        db.commit()
    response = mutate(
        context,
        principal("tenant-a", "public_review.resolve"),
        annotation_id,
        operation,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "review_issue_not_found",
        "message": "Review issue is unavailable",
    }
    assert audit_rows(context[2]) == []


def test_exact_source_pair_is_preserved_by_mutation(context):
    seed_standard(context[2])
    response = mutate(
        context,
        principal("tenant-a", "public_review.resolve"),
        "root-source-a2",
        "resolve",
    )
    assert response.status_code == 200
    with context[2]() as db:
        source_a = db.get(AssetAnnotationModel, "root-open")
        source_a2 = db.get(AssetAnnotationModel, "root-source-a2")
        assert source_a.status == "open"
        assert source_a.source_asset_id == "source-asset-a"
        assert source_a2.status == "resolved"
        assert source_a2.source_asset_id == "source-asset-a2"
    event = audit_rows(context[2], "review_issue_resolved")[0]
    assert event.detail_json["source_asset_id"] == "source-asset-a2"


@pytest.mark.parametrize(
    ("operation", "target", "initial_status"),
    [
        ("resolve", "root-open", "open"),
        ("reopen", "root-resolved", "resolved"),
    ],
)
def test_status_and_audit_roll_back_together_on_audit_failure(
    context, operation, target, initial_status
):
    seed_standard(context[2])
    app, client, factory, _ = context
    actor = principal("tenant-a", "public_review.resolve")
    app.dependency_overrides[require_authenticated_principal] = lambda: actor

    def fail_after_audit_flush(repository, **values):
        repository.session.add(
            AuthAuditEventModel(
                tenant_id=values["tenant_id"],
                actor_id=values["actor_id"],
                action=values["action"],
                detail_json={"annotation_id": values["issue"].id},
            )
        )
        repository.session.flush()
        raise RuntimeError("simulated audit persistence failure")

    try:
        with patch(
            "app.modules.public_review.board_router.SessionLocal", factory
        ), patch.object(
            BoardRepository,
            "audit_issue_transition",
            autospec=True,
            side_effect=fail_after_audit_flush,
        ):
            with pytest.raises(RuntimeError, match="simulated audit"):
                client.post(f"{BOARD}/issues/{target}/{operation}")
    finally:
        app.dependency_overrides.clear()

    with factory() as db:
        issue = db.get(AssetAnnotationModel, target)
        assert issue.status == initial_status
        if initial_status == "open":
            assert issue.resolved_at is None
            assert issue.resolved_by is None
        else:
            assert issue.resolved_at is not None
            assert issue.resolved_by == "internal-user-a"
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuthAuditEventModel)
                .where(AuthAuditEventModel.tenant_id == "tenant-a")
            )
            == 0
        )


def test_stats_recompute_for_final_and_nonfinal_open_pair_and_share(context):
    seed_standard(context[2])
    read_actor = principal(
        "tenant-a", "public_review.read", "public_review.resolve"
    )

    initial = api_request(
        context, read_actor, "GET", f"{BOARD}/stats"
    ).json()
    assert initial == {
        "total_issues": 3,
        "open_issues": 2,
        "resolved_issues": 1,
        "resolution_rate": 33,
        "assets_with_open_issues": 2,
        "shares_with_open_issues": 2,
    }

    with context[2]() as db:
        add_annotation(db, annotation_id="root-open-2")
        db.commit()

    mutate(context, read_actor, "root-open", "resolve")
    nonfinal = api_request(
        context, read_actor, "GET", f"{BOARD}/stats"
    ).json()
    assert nonfinal["total_issues"] == 4
    assert nonfinal["open_issues"] == 2
    assert nonfinal["resolved_issues"] == 2
    assert nonfinal["assets_with_open_issues"] == 2
    assert nonfinal["shares_with_open_issues"] == 2

    mutate(context, read_actor, "root-open-2", "resolve")
    final = api_request(
        context, read_actor, "GET", f"{BOARD}/stats"
    ).json()
    assert final["total_issues"] == 4
    assert final["open_issues"] == 1
    assert final["resolved_issues"] == 3
    assert final["assets_with_open_issues"] == 1
    assert final["shares_with_open_issues"] == 1

    mutate(context, read_actor, "root-open-2", "reopen")
    reopened = api_request(
        context, read_actor, "GET", f"{BOARD}/stats"
    ).json()
    assert reopened["total_issues"] == 4
    assert reopened["open_issues"] == 2
    assert reopened["resolved_issues"] == 2
    assert reopened["assets_with_open_issues"] == 2
    assert reopened["shares_with_open_issues"] == 2


def test_list_and_detail_immediately_reflect_resolve_and_reopen(context):
    seed_standard(context[2])
    actor = principal(
        "tenant-a", "public_review.read", "public_review.resolve"
    )
    mutate(context, actor, "root-open", "resolve")

    open_ids = {
        item["id"]
        for item in api_request(
            context, actor, "GET", f"{BOARD}/issues?status=open"
        ).json()["items"]
    }
    resolved_ids = {
        item["id"]
        for item in api_request(
            context, actor, "GET", f"{BOARD}/issues?status=resolved"
        ).json()["items"]
    }
    detail = api_request(
        context, actor, "GET", f"{BOARD}/issues/root-open"
    ).json()
    assert "root-open" not in open_ids
    assert "root-open" in resolved_ids
    assert detail["status"] == "resolved"
    assert detail["resolver"] == {"actor_id": "user-tenant-a"}

    mutate(context, actor, "root-open", "reopen")
    open_ids = {
        item["id"]
        for item in api_request(
            context, actor, "GET", f"{BOARD}/issues?status=open"
        ).json()["items"]
    }
    detail = api_request(
        context, actor, "GET", f"{BOARD}/issues/root-open"
    ).json()
    assert "root-open" in open_ids
    assert detail["status"] == "open"
    assert detail["resolved_at"] is None
    assert detail["resolver"] is None


def test_audit_detail_is_bounded_and_contains_no_sensitive_data(context):
    seed_standard(context[2])
    mutate(
        context,
        principal("tenant-a", "public_review.resolve"),
        "root-open",
        "resolve",
    )
    mutate(
        context,
        principal("tenant-a", "public_review.resolve"),
        "root-open",
        "reopen",
    )
    rows = audit_rows(context[2])
    assert [row.action for row in rows] == [
        "review_issue_resolved",
        "review_issue_reopened",
    ]
    allowed = {
        "annotation_id",
        "share_id",
        "asset_id",
        "source_asset_id",
        "old_status",
        "new_status",
    }
    forbidden = {
        "content_json",
        "plain_text",
        "guest_id",
        "secret",
        "secret_digest",
        "session",
        "session_digest",
        "credential",
        "source_metadata",
        "url",
        "path",
    }
    for row in rows:
        assert set(row.detail_json) == allowed
        serialized = str(row.detail_json).casefold()
        assert all(value not in serialized for value in forbidden)
        assert len(serialized) < 512


def test_postgresql_mutation_query_contains_row_lock():
    statement = BoardRepository._mutation_query(
        tenant_id="tenant-a", annotation_id="root-open"
    ).with_for_update(of=AssetAnnotationModel)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE OF asset_annotations" in sql
    assert "asset_source_links.asset_id = asset_annotations.asset_id" in sql
    assert (
        "asset_source_links.source_asset_id = asset_annotations.source_asset_id"
        in sql
    )
    assert "asset_annotations.tenant_id = 'tenant-a'" in sql

