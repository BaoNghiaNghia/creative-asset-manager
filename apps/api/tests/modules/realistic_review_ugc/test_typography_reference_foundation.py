"""Typography V2 G1.1: dedup, tenant isolation, visibility and rights gates."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.authorization.seed import (
    PERMISSION_DEFINITIONS, VIEWER_PERMISSIONS, OPERATOR_PERMISSIONS,
    SYSTEM_ROLE_DEFINITIONS,
)
from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel, RrugcTypographyStyleDnaModel,
)
from app.modules.realistic_review_ugc.typography_repository import (
    TypographyReferenceError, TypographyReferenceRepository,
)


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    RrugcReferenceAssetModel.__table__.create(engine)
    RrugcTypographyReferenceModel.__table__.create(engine)
    RrugcTypographyStyleDnaModel.__table__.create(engine)
    yield engine
    engine.dispose()


def asset(session: Session, tenant: str, identifier: str, sha: str, *, profile="realistic-person-ugc"):
    row = RrugcReferenceAssetModel(
        id=identifier, tenant_id=tenant, source_type="upload", source_key=sha,
        content_hash=sha, profile_key=profile, reference_type="other", status="ready",
        remote_file_id="drive-" + identifier, created_by_user_id="owner",
    )
    session.add(row)
    session.flush()
    return row


def test_typography_link_does_not_mutate_ugc_profile_or_create_new_storage(engine):
    with Session(engine) as session:
        base = asset(session, "tenant-a", "asset-a", "a" * 64)
        library = TypographyReferenceRepository(session)
        ref, created = library.link_asset(
            tenant_id="tenant-a", workspace_id="tenant-a", asset_id=base.id,
            user_id="owner", rights="unknown",
        )
        assert created
        assert ref.reference_roles_json == ["layout", "font_mood", "overlap"]
        assert ref.influence == "balanced"
        assert ref.editorial_status == "candidate"
        assert base.profile_key == "realistic-person-ugc"
        again, created = library.link_asset(
            tenant_id="tenant-a", workspace_id="tenant-a", asset_id=base.id,
            user_id="owner", rights="owned",
        )
        assert not created and again.id == ref.id
        assert again.source_rights_status == "unknown"  # dedup cannot self-upgrade rights
        session.commit()


def test_get_list_hash_and_dna_are_tenant_scoped_and_private(engine):
    with Session(engine) as session:
        a = asset(session, "tenant-a", "asset-a", "a" * 64)
        b = asset(session, "tenant-b", "asset-b", "a" * 64)
        repo = TypographyReferenceRepository(session)
        row_a, _ = repo.link_asset(
            tenant_id="tenant-a", workspace_id="tenant-a", asset_id=a.id,
            user_id="owner", privacy="private",
        )
        row_b, _ = repo.link_asset(
            tenant_id="tenant-b", workspace_id="tenant-b", asset_id=b.id,
            user_id="other", privacy="workspace",
        )
        session.flush()
        assert repo.get("tenant-a", row_a.id, user_id="owner") is row_a
        assert repo.get("tenant-b", row_a.id, user_id="other", manage=True) is None
        assert repo.get("tenant-a", row_a.id, user_id="visitor") is None
        assert repo.get("tenant-a", row_a.id, user_id="visitor", manage=True) is row_a
        assert repo.by_hash("tenant-a", a.content_hash, user_id="visitor") is None
        assert repo.by_hash("tenant-b", b.content_hash, user_id="other") is row_b
        assert repo.list("tenant-a", user_id="visitor")[1] == 0
        assert repo.list("tenant-b", user_id="other")[1] == 1
        assert repo.dna_versions("tenant-b", row_a.id, user_id="other", manage=True) == []


def test_cross_tenant_link_is_denied_and_db_fk_enforces_tenant(engine):
    with Session(engine) as session:
        a = asset(session, "tenant-a", "asset-a", "a" * 64)
        with pytest.raises(TypographyReferenceError) as blocked:
            TypographyReferenceRepository(session).link_asset(
                tenant_id="tenant-b", workspace_id="tenant-b",
                asset_id=a.id, user_id="owner",
            )
        assert blocked.value.code == "reference_asset_not_found"
        session.add(RrugcTypographyReferenceModel(
            tenant_id="tenant-b", workspace_id="tenant-b", asset_id=a.id,
            content_hash="b" * 64, created_by_user_id="owner",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_editorial_requires_verified_rights_and_cannot_cross_tenant(engine):
    with Session(engine) as session:
        a = asset(session, "tenant-a", "asset-a", "a" * 64)
        repo = TypographyReferenceRepository(session)
        row, _ = repo.link_asset(
            tenant_id="tenant-a", workspace_id="tenant-a", asset_id=a.id,
            user_id="owner", rights="unknown",
        )
        with pytest.raises(TypographyReferenceError) as unverified:
            repo.update_editorial("tenant-a", row.id, user_id="manager", status="approved")
        assert unverified.value.code == "reference_rights_unverified"
        with pytest.raises(TypographyReferenceError) as no_cross_tenant:
            repo.update_editorial("tenant-b", row.id, user_id="manager", status="approved", rights="owned")
        assert no_cross_tenant.value.code == "reference_not_found"
        saved = repo.update_editorial(
            "tenant-a", row.id, user_id="manager", status="approved", rights="licensed",
        )
        assert saved.editorial_status == "approved"
        assert saved.source_rights_status == "licensed"
        repo.update_editorial("tenant-a", row.id, user_id="manager", status="archived")
        assert repo.list("tenant-a", user_id="manager", manage=True)[1] == 0
        assert repo.list("tenant-a", user_id="manager", manage=True, status="archived")[1] == 1


def test_dna_version_persisted_under_tenant_and_unique_analyzer_hash(engine):
    with Session(engine) as session:
        a = asset(session, "tenant-a", "asset-a", "a" * 64)
        repo = TypographyReferenceRepository(session)
        row, _ = repo.link_asset(
            tenant_id="tenant-a", workspace_id="tenant-a", asset_id=a.id,
            user_id="owner",
        )
        version = RrugcTypographyStyleDnaModel(
            tenant_id="tenant-a", reference_id=row.id,
            analyzer_version="analyzer-v1", image_hash=a.content_hash,
            typography_region_json={"x": .1, "y": .2, "w": .6, "h": .3},
            style_family="serif-script", font_mood_json={"primary": "serif"},
            layout_graph_json={"nodes": [], "edges": []}, overlap_rules_json=[],
            confidence=.8, analyzed_at=datetime.now(timezone.utc),
        )
        session.add(version)
        session.flush()
        assert repo.dna_versions("tenant-a", row.id, user_id="owner")[0] is version
        assert repo.dna_versions("tenant-b", row.id, user_id="owner", manage=True) == []
        session.add(RrugcTypographyStyleDnaModel(
            tenant_id="tenant-a", reference_id=row.id,
            analyzer_version="analyzer-v1", image_hash=a.content_hash,
            typography_region_json={}, style_family="serif-script",
            font_mood_json={}, layout_graph_json={}, overlap_rules_json=[],
            confidence=.9,
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_new_typography_privileges_are_separate_from_existing_viewer_operator():
    rights = {
        "typography_reference.read", "typography_reference.upload",
        "typography_reference.manage", "typography_generate.run",
        "typography_scout.manage",
    }
    assert rights.issubset(PERMISSION_DEFINITIONS)
    assert rights.isdisjoint(VIEWER_PERMISSIONS)
    assert rights.isdisjoint(OPERATOR_PERMISSIONS)
    assert rights.issubset(SYSTEM_ROLE_DEFINITIONS["tenant_admin"][2])
