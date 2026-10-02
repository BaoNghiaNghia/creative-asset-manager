from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.router import get_source_plans
from app.modules.realistic_review_ugc.source_plans import (
    RRUGC_SOURCE_ROOT_FOLDER_ID,
    RRUGC_SOURCE_TARGET_COUNT,
    discover_source_images,
    sync_source_plans,
)
from app.providers.google.storage import GoogleDriveAssetStorage


def node(
    item_id: str,
    name: str,
    kind: str,
    *,
    parent_id: str | None = None,
    mime_type: str | None = None,
):
    return SimpleNamespace(
        id=item_id,
        name=name,
        kind=kind,
        parent_id=parent_id,
        mime_type=mime_type or (
            "application/vnd.google-apps.folder"
            if kind == "folder"
            else "image/jpeg"
        ),
        size=1234 if kind == "image" else None,
        modified_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        web_url=f"https://drive.google.com/file/d/{item_id}/view",
        image_width=1200 if kind == "image" else None,
        image_height=1200 if kind == "image" else None,
    )


class FakeDrive:
    def __init__(self, _token: str = "token"):
        self.root = node(RRUGC_SOURCE_ROOT_FOLDER_ID, "root", "folder")
        self.rows = {
            RRUGC_SOURCE_ROOT_FOLDER_ID: [
                node("direct-image", "ignore.jpg", "image", parent_id=RRUGC_SOURCE_ROOT_FOLDER_ID),
                node("folder-a", "Dogs", "folder", parent_id=RRUGC_SOURCE_ROOT_FOLDER_ID),
                node("folder-b", "Teachers", "folder", parent_id=RRUGC_SOURCE_ROOT_FOLDER_ID),
            ],
            "folder-a": [
                node("image-a", "dog-cap.jpg", "image", parent_id="folder-a"),
                node("nested", "Weekend", "folder", parent_id="folder-a"),
            ],
            "nested": [
                node("image-b", "camping-cap.jpg", "image", parent_id="nested"),
            ],
            "folder-b": [
                node("not-image", "notes.txt", "other", parent_id="folder-b", mime_type="text/plain"),
                node("image-c", "teacher-cap.png", "image", parent_id="folder-b", mime_type="image/png"),
            ],
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, item_id: str):
        assert item_id == RRUGC_SOURCE_ROOT_FOLDER_ID
        return self.root

    async def children(self, parent_id: str):
        return list(self.rows.get(parent_id, []))


class FakeDriveMissingTeacher(FakeDrive):
    def __init__(self, _token: str = "token"):
        super().__init__(_token)
        self.rows["folder-b"] = [
            node(
                "not-image",
                "notes.txt",
                "other",
                parent_id="folder-b",
                mime_type="text/plain",
            )
        ]


class FakeDriveModifiedDog(FakeDrive):
    def __init__(self, _token: str = "token"):
        super().__init__(_token)
        changed = node("image-a", "dog-cap.jpg", "image", parent_id="folder-a")
        changed.modified_at = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.rows["folder-a"][0] = changed


class FakeStorage(GoogleDriveAssetStorage):
    async def get_access_token(self) -> str:
        return "managed-token"


def make_database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TenantProcessingPolicyModel.__table__.create(engine)
    ProcessingJobModel.__table__.create(engine)
    RrugcCampaignModel.__table__.create(engine)
    RrugcSourcePlanModel.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_source_plan_list_is_server_paginated_and_searchable():
    factory = make_database()

    with factory() as session:
        session.add_all(
            [
                RrugcSourcePlanModel(
                    tenant_id="tenant-a",
                    root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                    source_file_id="image-a",
                    source_parent_folder_id="folder-a",
                    source_relative_path="Dogs/dog-cap.jpg",
                    source_name="dog-cap.jpg",
                    source_mime_type="image/jpeg",
                    source_revision="a" * 64,
                    analysis_revision=1,
                    target_count=20,
                    status="ready",
                    created_by_user_id="user-a",
                ),
                RrugcSourcePlanModel(
                    tenant_id="tenant-a",
                    root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                    source_file_id="image-b",
                    source_parent_folder_id="folder-b",
                    source_relative_path="Teachers/teacher-cap.png",
                    source_name="teacher-cap.png",
                    source_mime_type="image/png",
                    source_revision="b" * 64,
                    analysis_revision=1,
                    target_count=20,
                    status="ready",
                    created_by_user_id="user-a",
                ),
                RrugcSourcePlanModel(
                    tenant_id="tenant-a",
                    root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                    source_file_id="image-c",
                    source_parent_folder_id="folder-c",
                    source_relative_path="Weekend/camping-cap.jpg",
                    source_name="camping-cap.jpg",
                    source_mime_type="image/jpeg",
                    source_revision="c" * 64,
                    analysis_revision=1,
                    target_count=20,
                    status="ready",
                    created_by_user_id="user-a",
                ),
                RrugcSourcePlanModel(
                    tenant_id="tenant-b",
                    root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                    source_file_id="other-tenant",
                    source_parent_folder_id="folder-z",
                    source_relative_path="Other/hidden.jpg",
                    source_name="hidden.jpg",
                    source_mime_type="image/jpeg",
                    source_revision="d" * 64,
                    analysis_revision=1,
                    target_count=20,
                    status="ready",
                    created_by_user_id="user-b",
                ),
            ]
        )
        session.commit()
        principal = SimpleNamespace(active_tenant_id="tenant-a")

        page = get_source_plans(
            page=2,
            page_size=1,
            q=None,
            session=session,
            principal=principal,
        )
        assert page.total == 3
        assert page.page == 2
        assert page.page_size == 1
        assert [item.source_relative_path for item in page.items] == [
            "Teachers/teacher-cap.png"
        ]

        search = get_source_plans(
            page=1,
            page_size=20,
            q="teacher",
            session=session,
            principal=principal,
        )
        assert search.total == 1
        assert [item.source_name for item in search.items] == ["teacher-cap.png"]


def test_discover_source_images_recurses_child_folders_and_ignores_root_images():
    rows, folders = asyncio.run(discover_source_images(FakeDrive()))

    assert folders == 3
    assert [row.file_id for row in rows] == ["image-a", "image-b", "image-c"]
    assert [row.relative_path for row in rows] == [
        "Dogs/dog-cap.jpg",
        "Dogs/Weekend/camping-cap.jpg",
        "Teachers/teacher-cap.png",
    ]
    assert all(len(row.revision) == 64 for row in rows)


def test_sync_source_plans_creates_one_twenty_ref_plan_per_source_image_idempotently():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TenantProcessingPolicyModel.__table__.create(engine)
    ProcessingJobModel.__table__.create(engine)
    RrugcCampaignModel.__table__.create(engine)
    RrugcSourcePlanModel.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    with factory() as session:
        result = asyncio.run(
            sync_source_plans(
                session,
                tenant_id="tenant-a",
                user_id="user-a",
                storage=FakeStorage.__new__(FakeStorage),
                drive_client_factory=FakeDrive,
            )
        )
        assert result.images_found == 3
        assert result.plans_created == 3
        assert result.jobs_queued == 3

        plans = list(session.scalars(select(RrugcSourcePlanModel)))
        jobs = list(session.scalars(select(ProcessingJobModel)))
        assert len(plans) == 3
        assert len(jobs) == 3
        assert {plan.target_count for plan in plans} == {RRUGC_SOURCE_TARGET_COUNT}
        assert {job.job_type for job in jobs} == {"rrugc_source_plan_analyze"}

        second = asyncio.run(
            sync_source_plans(
                session,
                tenant_id="tenant-a",
                user_id="user-a",
                storage=FakeStorage.__new__(FakeStorage),
                drive_client_factory=FakeDrive,
            )
        )
        assert second.plans_created == 0
        assert second.plans_updated == 0
        assert second.jobs_queued == 0
        assert second.unchanged == 3
        assert len(list(session.scalars(select(RrugcSourcePlanModel)))) == 3
        assert len(list(session.scalars(select(ProcessingJobModel)))) == 3


def test_sync_source_plans_marks_removed_source_missing_and_archives_campaign():
    factory = make_database()

    with factory() as session:
        asyncio.run(
            sync_source_plans(
                session,
                tenant_id="tenant-a",
                user_id="user-a",
                storage=FakeStorage.__new__(FakeStorage),
                drive_client_factory=FakeDrive,
            )
        )
        plan = session.scalar(
            select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.source_file_id == "image-c"
            )
        )
        assert plan is not None
        campaign = RrugcCampaignModel(
            tenant_id="tenant-a",
            name="Teacher reference campaign",
            query="teacher lifestyle phone photo",
            target_count=20,
            auto_scout=True,
            scan_next_at=datetime.now(timezone.utc),
            scout_token_hash="a" * 64,
            created_by_user_id="user-a",
        )
        session.add(campaign)
        session.flush()
        plan.campaign_id = campaign.id
        session.commit()

        result = asyncio.run(
            sync_source_plans(
                session,
                tenant_id="tenant-a",
                user_id="user-a",
                storage=FakeStorage.__new__(FakeStorage),
                drive_client_factory=FakeDriveMissingTeacher,
            )
        )
        session.refresh(plan)
        session.refresh(campaign)

        assert result.images_found == 2
        assert result.plans_missing == 1
        assert plan.status == "missing"
        assert plan.last_error_code == "rrugc_source_missing"
        assert campaign.status == "archived"
        assert campaign.auto_scout is False
        assert campaign.scan_next_at is None


def test_changed_source_image_gets_new_analysis_revision_and_job():
    factory = make_database()

    with factory() as session:
        asyncio.run(
            sync_source_plans(
                session,
                tenant_id="tenant-a",
                user_id="user-a",
                storage=FakeStorage.__new__(FakeStorage),
                drive_client_factory=FakeDrive,
            )
        )
        plan = session.scalar(
            select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.source_file_id == "image-a"
            )
        )
        assert plan is not None
        original_revision = plan.source_revision
        original_analysis_revision = plan.analysis_revision

        result = asyncio.run(
            sync_source_plans(
                session,
                tenant_id="tenant-a",
                user_id="user-a",
                storage=FakeStorage.__new__(FakeStorage),
                drive_client_factory=FakeDriveModifiedDog,
            )
        )
        session.refresh(plan)

        assert result.plans_updated == 1
        assert result.jobs_queued == 1
        assert plan.source_revision != original_revision
        assert plan.analysis_revision == original_analysis_revision + 1
        assert plan.status == "queued"
        jobs = list(
            session.scalars(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.entity_id == plan.id,
                    ProcessingJobModel.job_type == "rrugc_source_plan_analyze",
                )
            )
        )
        assert len(jobs) == 2
