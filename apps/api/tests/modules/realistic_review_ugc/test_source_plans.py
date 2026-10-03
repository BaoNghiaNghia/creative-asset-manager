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
    RrugcCandidateModel,
    RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.router import (
    _source_plan_reference_preview,
    get_source_plans,
)
from app.modules.realistic_review_ugc.source_plans import (
    RRUGC_SOURCE_ROOT_FOLDER_ID,
    RRUGC_SOURCE_TARGET_COUNT,
    _reconcile_embroidery_groups,
    _synthetic_product_snapshot,
    _synthetic_reference_snapshot,
    discover_source_images,
    embroidery_signature,
    sync_source_plans,
)
from app.modules.realistic_review_ugc.product_context import (
    PRODUCT_CONTEXT_PROFILE_VERSION,
    PRODUCT_VISUAL_CONTEXT_VERSION,
    product_visual_binding_fingerprint,
)
from app.modules.realistic_review_ugc.service import RrugcService
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
    RrugcCandidateModel.__table__.create(engine)
    RrugcSourcePlanModel.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_source_plan_visual_binding_uses_numeric_revision_and_hash_content():
    plan = RrugcSourcePlanModel(
        tenant_id="tenant-a",
        root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
        source_file_id="image-hash",
        source_parent_folder_id="folder-a",
        source_relative_path="Dogs/design.webp",
        source_name="design.webp",
        source_mime_type="image/webp",
        source_revision="e25b0b8085e4c2e71b55385a7d0c40a578476dee1a119b42dd634e7eb1bda28a",
        analysis_revision=3,
        target_count=20,
        status="queued",
        created_by_user_id="user-a",
    )

    product = _synthetic_product_snapshot(plan)
    references = _synthetic_reference_snapshot(plan)

    assert product["revision"] == 3
    assert references[0]["version"] == 3
    assert references[0]["content_hash"] == plan.source_revision
    assert len(product_visual_binding_fingerprint(product, references)) == 64



def test_reconcile_refreshes_stale_source_campaign_profile_without_gemini():
    factory = make_database()
    with factory() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Old hat source campaign",
            query="old query",
            target_count=RRUGC_SOURCE_TARGET_COUNT,
            max_scroll_batches=2,
            auto_import=True,
            discovery_mode="product_context",
            auto_scout=True,
            commit=False,
        )
        campaign.product_context_json = {
            "version": "product-context-v4-embroidery-text-search",
            "auto_context": True,
        }
        plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
            source_file_id="hat-refresh",
            source_parent_folder_id="folder-a",
            source_relative_path="Hats/Whiskey/whiskey-cap.jpg",
            source_name="whiskey-cap.jpg",
            source_mime_type="image/jpeg",
            source_revision="d" * 64,
            analysis_revision=3,
            target_count=RRUGC_SOURCE_TARGET_COUNT,
            status="ready",
            created_by_user_id="user-a",
            campaign_id=campaign.id,
            embroidery_signature="whiskey-signature",
            visual_context_json={},
            analyzed_at=datetime.now(timezone.utc),
        )
        plan.visual_context_json = {
            "version": PRODUCT_VISUAL_CONTEXT_VERSION,
            "status": "ready",
            "binding_fingerprint": product_visual_binding_fingerprint(
                _synthetic_product_snapshot(plan),
                _synthetic_reference_snapshot(plan),
            ),
            "themes": [],
            "embroidery_text": ["WHISKEY HELL BOUND"],
            "scene_hints": [],
            "audience_hints": [],
            "occasion_hints": [],
            "product_cues": ["front embroidery"],
            "avoid_hints": [],
            "confidence": 0.95,
            "summary": "Readable embroidered hat front.",
        }
        session.add(plan)
        session.flush()

        _reconcile_embroidery_groups(
            session,
            tenant_id="tenant-a",
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
        )
        session.flush()

        assert campaign.product_context_json["version"] == PRODUCT_CONTEXT_PROFILE_VERSION
        assert campaign.product_context_json["reference_contexts"] == ["hand_holding_hat"]
        assert any(
            "held in hand" in query.casefold()
            for query in campaign.search_queries_json
        )
        assert campaign.search_query_anchors_json[0] == (
            "WHISKEY HELL BOUND embroidered hat held in hand front view"
        )


def test_embroidery_signature_ignores_hat_color_but_keeps_distinct_motifs():
    navy = {
        "embroidery_text": ["Bad Day To Be A Hotdog"],
        "themes": ["humor"],
        "product_cues": ["navy cap with hotdog motif and embroidered wording"],
        "summary": "Navy hat with a hotdog graphic and funny text.",
    }
    red = {
        "embroidery_text": ["Bad Day To Be A Hotdog"],
        "themes": ["humor"],
        "product_cues": ["red hat with hotdog artwork and stitched wording"],
        "summary": "Red cap with the same hotdog graphic and phrase.",
    }
    golf = {
        "embroidery_text": ["Bad Day To Be A Hotdog"],
        "themes": ["golf"],
        "product_cues": ["black cap with golf ball and club artwork"],
        "summary": "Black cap with a golf motif and the phrase.",
    }

    assert embroidery_signature(navy) == embroidery_signature(red)
    assert embroidery_signature(navy) != embroidery_signature(golf)


def test_reconcile_embroidery_groups_shares_best_campaign_and_reopens_to_fifty():
    factory = make_database()
    signature = embroidery_signature({
        "embroidery_text": ["Best Grandpa By Par"],
        "themes": ["golf", "grandparent"],
        "product_cues": ["golf club and ball motif"],
    })
    assert signature is not None

    with factory() as session:
        sparse = RrugcCampaignModel(
            tenant_id="tenant-a",
            name="Sparse Navy",
            query="grandpa golf candid",
            target_count=20,
            auto_import=True,
            auto_scout=True,
            status="completed",
            scout_token_hash="1" * 64,
            created_by_user_id="user-a",
        )
        richer = RrugcCampaignModel(
            tenant_id="tenant-a",
            name="Richer Black",
            query="grandpa golf phone photo",
            target_count=20,
            auto_import=True,
            auto_scout=True,
            status="completed",
            scout_token_hash="2" * 64,
            created_by_user_id="user-a",
        )
        session.add_all([sparse, richer])
        session.flush()

        navy_plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
            source_file_id="navy-source",
            source_relative_path="Grandpa/Navy.jpg",
            source_name="Navy.jpg",
            source_mime_type="image/jpeg",
            source_revision="a" * 64,
            analysis_revision=1,
            embroidery_signature=signature,
            target_count=20,
            status="ready",
            visual_context_json={"status": "ready", "embroidery_text": ["Best Grandpa By Par"]},
            campaign_id=sparse.id,
            created_by_user_id="user-a",
        )
        black_plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
            source_file_id="black-source",
            source_relative_path="Grandpa/Black.jpg",
            source_name="Black.jpg",
            source_mime_type="image/jpeg",
            source_revision="b" * 64,
            analysis_revision=1,
            embroidery_signature=signature,
            target_count=20,
            status="ready",
            visual_context_json={"status": "ready", "embroidery_text": ["Best Grandpa By Par"]},
            campaign_id=richer.id,
            created_by_user_id="user-a",
        )
        session.add_all([navy_plan, black_plan])
        session.flush()

        session.add(
            RrugcCandidateModel(
                tenant_id="tenant-a",
                campaign_id=sparse.id,
                source_key="sparse-ref",
                pin_url="https://www.pinterest.com/pin/sparse/",
                image_url="https://i.pinimg.com/736x/sparse.jpg",
                status="drive_ready",
            )
        )
        for index in range(2):
            session.add(
                RrugcCandidateModel(
                    tenant_id="tenant-a",
                    campaign_id=richer.id,
                    source_key=f"rich-ref-{index}",
                    pin_url=f"https://www.pinterest.com/pin/rich-{index}/",
                    image_url=f"https://i.pinimg.com/736x/rich-{index}.jpg",
                    status="drive_ready",
                )
            )
        session.commit()

        _reconcile_embroidery_groups(
            session,
            tenant_id="tenant-a",
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
        )
        session.commit()
        session.refresh(navy_plan)
        session.refresh(black_plan)
        session.refresh(sparse)
        session.refresh(richer)

        assert navy_plan.campaign_id == richer.id
        assert black_plan.campaign_id == richer.id
        assert navy_plan.target_count == RRUGC_SOURCE_TARGET_COUNT == 50
        assert black_plan.target_count == 50
        assert richer.target_count == 50
        assert richer.status == "running"
        assert richer.auto_scout is True
        assert sparse.auto_scout is False
        assert sparse.status == "archived"



def test_source_plan_reference_preview_exposes_positive_negative_and_neutral_feedback():
    created_at = datetime(2026, 10, 3, tzinfo=timezone.utc)
    picked = _source_plan_reference_preview(SimpleNamespace(
        id="candidate-picked",
        pin_url="https://www.pinterest.com/pin/123/",
        image_url="https://i.pinimg.com/736x/example.jpg",
        status="drive_ready",
        width=900,
        height=1200,
        created_at=created_at,
        ai_signal_json={
            "scout_query": "grandpa golf course candid phone photo",
            "reference_manual_label": "good",
        },
    ))
    rejected = _source_plan_reference_preview(SimpleNamespace(
        id="candidate-rejected",
        pin_url="https://www.pinterest.com/pin/234/",
        image_url="https://i.pinimg.com/736x/example-bad.jpg",
        status="drive_ready",
        width=900,
        height=1200,
        created_at=created_at,
        ai_signal_json={
            "scout_query": "grandpa golf course candid phone photo",
            "reference_manual_label": "bad",
        },
    ))
    neutral = _source_plan_reference_preview(SimpleNamespace(
        id="candidate-neutral",
        pin_url="https://www.pinterest.com/pin/456/",
        image_url="https://i.pinimg.com/736x/example-2.jpg",
        status="approved",
        width=900,
        height=1200,
        created_at=created_at,
        ai_signal_json={"scout_query": "grandpa golf course candid phone photo"},
    ))

    assert picked.picked is True
    assert picked.rejected is False
    assert picked.source_query == "grandpa golf course candid phone photo"
    assert rejected.picked is False
    assert rejected.rejected is True
    assert neutral.picked is False
    assert neutral.rejected is False


def test_source_plan_list_hides_negative_and_ai_refs_and_excludes_them_from_usable_progress():
    factory = make_database()

    with factory() as session:
        campaign = RrugcCampaignModel(
            tenant_id="tenant-a",
            name="Source row feedback campaign",
            query="casual cookout phone photo",
            target_count=20,
            auto_import=True,
            auto_scout=True,
            scout_token_hash="c" * 64,
            created_by_user_id="user-a",
        )
        session.add(campaign)
        session.flush()
        plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
            source_file_id="feedback-image",
            source_parent_folder_id="feedback-folder",
            source_relative_path="Feedback/cookout-cap.jpg",
            source_name="cookout-cap.jpg",
            source_mime_type="image/jpeg",
            source_revision="f" * 64,
            analysis_revision=1,
            target_count=20,
            status="ready",
            campaign_id=campaign.id,
            created_by_user_id="user-a",
        )
        sibling_plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
            source_file_id="feedback-image-red",
            source_parent_folder_id="feedback-folder",
            source_relative_path="Feedback/alternate-color.jpg",
            source_name="alternate-color.jpg",
            source_mime_type="image/jpeg",
            source_revision="e" * 64,
            analysis_revision=1,
            embroidery_signature="shared-signature",
            target_count=50,
            status="ready",
            campaign_id=campaign.id,
            created_by_user_id="user-a",
        )
        plan.embroidery_signature = "shared-signature"
        session.add_all([plan, sibling_plan])
        session.add_all([
            RrugcCandidateModel(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                source_key="1" * 64,
                pin_url="https://www.pinterest.com/pin/1001/",
                image_url="https://i.pinimg.com/736x/usable.jpg",
                status="drive_ready",
                ai_signal_json={"scout_query": "casual cookout phone photo"},
            ),
            RrugcCandidateModel(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                source_key="2" * 64,
                pin_url="https://www.pinterest.com/pin/1002/",
                image_url="https://i.pinimg.com/736x/rejected.jpg",
                status="drive_ready",
                ai_signal_json={
                    "scout_query": "casual cookout phone photo",
                    "reference_manual_label": "bad",
                },
            ),
            RrugcCandidateModel(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                source_key="3" * 64,
                pin_url="https://www.pinterest.com/pin/1003/",
                image_url="https://i.pinimg.com/736x/pending.jpg",
                status="analysis_queued",
                ai_signal_json={"scout_query": "casual cookout phone photo"},
            ),
            RrugcCandidateModel(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                source_key="4" * 64,
                pin_url="https://www.pinterest.com/pin/1004/",
                image_url="https://i.pinimg.com/736x/ai-rejected.jpg",
                status="drive_ready",
                ai_signal_json={
                    "scout_query": "casual cookout phone photo",
                    "reference_manual_label": "ai",
                },
                ai_manual_label="ai",
            ),
        ])
        session.commit()

        page = get_source_plans(
            page=1,
            page_size=20,
            q=None,
            session=session,
            principal=SimpleNamespace(active_tenant_id="tenant-a"),
        )

        assert page.total == 1
        item = page.items[0]
        assert item.drive_ready_count == 1
        assert item.progress_count == 1
        assert item.pending_ai_count == 1
        assert item.embroidery_signature == "shared-signature"
        assert item.embroidery_group_size == 2
        assert [source.source_name for source in item.source_group_images] == [
            "alternate-color.jpg",
            "cookout-cap.jpg",
        ]
        assert len(item.reference_previews) == 2
        assert all(reference.rejected is False for reference in item.reference_previews)
        assert [row.status for row in item.reference_previews] == [
            "drive_ready",
            "analysis_queued",
        ]
        assert {row.image_url for row in item.reference_previews} == {
            "https://i.pinimg.com/736x/usable.jpg",
            "https://i.pinimg.com/736x/pending.jpg",
        }


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


def test_source_plan_list_supports_server_side_sorting_before_pagination():
    factory = make_database()

    with factory() as session:
        rows = [
            RrugcSourcePlanModel(
                tenant_id="tenant-a",
                root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                source_file_id="alpha-1",
                source_parent_folder_id="folder-a",
                source_relative_path="Alpha/front-navy.jpg",
                source_name="front-navy.jpg",
                source_mime_type="image/jpeg",
                source_revision="1" * 64,
                analysis_revision=1,
                embroidery_signature="alpha-signature",
                target_count=50,
                status="ready",
                created_by_user_id="user-a",
            ),
            RrugcSourcePlanModel(
                tenant_id="tenant-a",
                root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                source_file_id="alpha-2",
                source_parent_folder_id="folder-a",
                source_relative_path="Alpha/front-black.jpg",
                source_name="front-black.jpg",
                source_mime_type="image/jpeg",
                source_revision="2" * 64,
                analysis_revision=1,
                embroidery_signature="alpha-signature",
                target_count=50,
                status="ready",
                created_by_user_id="user-a",
            ),
            RrugcSourcePlanModel(
                tenant_id="tenant-a",
                root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                source_file_id="beta",
                source_parent_folder_id="folder-b",
                source_relative_path="Beta/front.jpg",
                source_name="front.jpg",
                source_mime_type="image/jpeg",
                source_revision="3" * 64,
                analysis_revision=1,
                target_count=50,
                status="queued",
                created_by_user_id="user-a",
            ),
            RrugcSourcePlanModel(
                tenant_id="tenant-a",
                root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                source_file_id="gamma",
                source_parent_folder_id="folder-c",
                source_relative_path="Gamma/front.jpg",
                source_name="front.jpg",
                source_mime_type="image/jpeg",
                source_revision="4" * 64,
                analysis_revision=1,
                target_count=50,
                status="failed",
                created_by_user_id="user-a",
            ),
        ]
        session.add_all(rows)
        session.commit()
        principal = SimpleNamespace(active_tenant_id="tenant-a")

        by_group_size = get_source_plans(
            page=1,
            page_size=1,
            q=None,
            sort_by="group_size",
            sort_dir="desc",
            session=session,
            principal=principal,
        )
        assert by_group_size.total == 3
        assert by_group_size.items[0].embroidery_signature == "alpha-signature"
        assert by_group_size.items[0].embroidery_group_size == 2

        by_source_desc = get_source_plans(
            page=1,
            page_size=20,
            q=None,
            sort_by="source",
            sort_dir="desc",
            session=session,
            principal=principal,
        )
        assert [
            item.source_relative_path.split("/", 1)[0]
            for item in by_source_desc.items
        ] == ["Gamma", "Beta", "Alpha"]


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


def test_sync_source_plans_creates_one_fifty_ref_plan_per_source_image_idempotently():
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


def test_sync_source_plans_deletes_removed_source_plan_and_only_its_jobs():
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
        plan_id = plan.id
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
        unrelated_job = ProcessingJobModel(
            tenant_id="tenant-a",
            job_type="asset_analyze",
            entity_type="asset",
            entity_id="asset-unrelated",
            idempotency_key="unrelated-job",
            priority=10,
        )
        session.add_all([campaign, unrelated_job])
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
        session.refresh(campaign)

        assert result.images_found == 2
        assert result.plans_missing == 1
        assert session.get(RrugcSourcePlanModel, plan_id) is None
        assert session.scalar(
            select(ProcessingJobModel.id).where(
                ProcessingJobModel.entity_type == "rrugc_source_plan",
                ProcessingJobModel.entity_id == plan_id,
            )
        ) is None
        assert session.get(ProcessingJobModel, unrelated_job.id) is not None
        assert campaign.status == "archived"
        assert campaign.auto_scout is False
        assert campaign.scan_next_at is None



def test_missing_color_does_not_archive_campaign_shared_by_another_source_plan():
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
        missing_plan = session.scalar(
            select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.source_file_id == "image-c"
            )
        )
        remaining_plan = session.scalar(
            select(RrugcSourcePlanModel).where(
                RrugcSourcePlanModel.source_file_id == "image-a"
            )
        )
        assert missing_plan is not None
        assert remaining_plan is not None
        missing_plan_id = missing_plan.id

        campaign = RrugcCampaignModel(
            tenant_id="tenant-a",
            name="Shared embroidery campaign",
            query="shared embroidery candid",
            target_count=50,
            auto_scout=True,
            status="running",
            scan_next_at=datetime.now(timezone.utc),
            scout_token_hash="c" * 64,
            created_by_user_id="user-a",
        )
        session.add(campaign)
        session.flush()
        missing_plan.campaign_id = campaign.id
        remaining_plan.campaign_id = campaign.id
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
        session.refresh(campaign)
        session.refresh(remaining_plan)

        assert result.plans_missing == 1
        assert session.get(RrugcSourcePlanModel, missing_plan_id) is None
        assert remaining_plan.campaign_id == campaign.id
        assert campaign.auto_scout is True
        assert campaign.status == "running"



def test_old_visual_context_version_requeues_source_for_text_aware_analysis():
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
        campaign = RrugcCampaignModel(
            tenant_id="tenant-a",
            name="Legacy source campaign",
            query="legacy context",
            target_count=20,
            auto_scout=True,
            scout_token_hash="b" * 64,
            created_by_user_id="user-a",
        )
        session.add(campaign)
        session.flush()
        plan.campaign_id = campaign.id
        plan.status = "ready"
        plan.visual_context_json = {
            "status": "ready",
            "version": "rrugc-product-visual-context-v1",
            "binding_fingerprint": "legacy",
        }
        plan.analyzed_at = datetime.now(timezone.utc)
        original_analysis_revision = plan.analysis_revision
        session.commit()

        result = asyncio.run(
            sync_source_plans(
                session,
                tenant_id="tenant-a",
                user_id="user-a",
                storage=FakeStorage.__new__(FakeStorage),
                drive_client_factory=FakeDrive,
            )
        )
        session.refresh(plan)

        assert PRODUCT_VISUAL_CONTEXT_VERSION == "rrugc-product-visual-context-v3-hand-held-hat"
        assert result.plans_updated == 1
        assert result.jobs_queued == 1
        assert plan.analysis_revision == original_analysis_revision + 1
        assert plan.status == "queued"
        assert plan.visual_context_json is None


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
