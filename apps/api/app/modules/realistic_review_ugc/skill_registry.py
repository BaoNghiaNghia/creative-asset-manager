from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.model import (
    RrugcStage2JobModel,
    RrugcKeywordImageJobModel,
    RrugcColorwayJobModel,
    RrugcStage2SkillRegistryModel,
    RrugcStage2SkillVersionModel,
    RrugcStageSkillDefaultModel,
)
from app.modules.realistic_review_ugc.stage2_skills import (
    Stage2SkillItem,
    Stage2SkillRegistryError,
    UploadedStage2SkillBundle,
    create_openai_stage2_skill_version,
    delete_openai_stage2_skill,
    delete_openai_stage2_skill_version,
    inspect_uploaded_stage2_skill_bundle,
    install_local_stage2_skill_bundle,
    installed_stage2_skill_sha256,
    list_openai_stage2_skill_versions,
    list_stage2_skill_catalog,
    set_openai_stage2_skill_default,
    sync_openai_stage2_skill,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    action: str,
    detail: dict[str, Any],
) -> None:
    session.add(
        AuthAuditEventModel(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            detail_json=detail,
        )
    )


def _registry_key(source: str, skill_name: str) -> tuple[str, str]:
    return str(source or "").strip().lower(), str(skill_name or "").strip()


def _version_row(
    session: Session,
    *,
    tenant_id: str,
    registry_id: str,
    version: str,
) -> RrugcStage2SkillVersionModel | None:
    return session.scalar(
        select(RrugcStage2SkillVersionModel).where(
            RrugcStage2SkillVersionModel.tenant_id == tenant_id,
            RrugcStage2SkillVersionModel.registry_id == registry_id,
            RrugcStage2SkillVersionModel.version == version,
        )
    )


def _sync_versions(
    session: Session,
    *,
    row: RrugcStage2SkillRegistryModel,
    versions: tuple[str, ...] | list[str],
) -> None:
    desired = [str(value).strip() for value in versions if str(value).strip()]
    existing = {
        item.version: item
        for item in session.scalars(
            select(RrugcStage2SkillVersionModel).where(
                RrugcStage2SkillVersionModel.tenant_id == row.tenant_id,
                RrugcStage2SkillVersionModel.registry_id == row.id,
            )
        )
    }
    for version in desired:
        item = existing.get(version)
        if item is None:
            item = RrugcStage2SkillVersionModel(
                id=str(uuid4()),
                tenant_id=row.tenant_id,
                registry_id=row.id,
                version=version,
                created_at=utcnow(),
            )
            session.add(item)
            existing[version] = item
        item.is_default = version == row.default_version
        item.is_synced = version == row.synced_version
        item.status = "available"
        if item.is_synced and row.bundle_sha256:
            item.bundle_sha256 = row.bundle_sha256
    for version, item in existing.items():
        item.is_default = version == row.default_version
        item.is_synced = version == row.synced_version
        if desired and version not in desired and not item.is_synced:
            item.status = "remote_missing"


def _upsert_catalog_item(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str | None,
    item: Stage2SkillItem,
    versions: tuple[str, ...] | None = None,
) -> RrugcStage2SkillRegistryModel:
    row = session.scalar(
        select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
            RrugcStage2SkillRegistryModel.source == item.source,
            RrugcStage2SkillRegistryModel.skill_name == item.skill_name,
        )
    )
    created = row is None
    if row is None:
        row = RrugcStage2SkillRegistryModel(
            id=str(uuid4()),
            tenant_id=tenant_id,
            source=item.source,
            skill_name=item.skill_name,
            display_name=item.display_name or item.skill_name,
            description=item.description or "",
            workflow="image_studio",
            enabled=True,
            created_by_user_id=actor_id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(row)
        session.flush()
    # A removed project-managed skill stays tombstoned after future source scans.
    if row.deleted_at is not None:
        return row
    row.skill_id = item.skill_id
    row.display_name = item.display_name or item.skill_name
    row.description = item.description or ""
    row.default_version = item.default_version
    row.latest_version = item.latest_version
    row.synced_version = item.synced_version
    row.sync_state = item.sync_state
    row.validation_status = "valid" if item.ready or item.sync_state != "local_conflict" else "conflict"
    row.last_error = None
    row.updated_by_user_id = actor_id
    row.updated_at = utcnow()
    if item.ready:
        row.bundle_sha256 = installed_stage2_skill_sha256(item.skill_name)
    version_values = versions if versions is not None else item.version_options
    _sync_versions(session, row=row, versions=version_values)
    if created and actor_id:
        _audit(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="stage2_skill.discovered",
            detail={
                "registry_id": row.id,
                "source": row.source,
                "skill_id": row.skill_id,
                "skill_name": row.skill_name,
            },
        )
    return row


def reconcile_skill_registry(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str | None = None,
    refresh: bool = False,
) -> list[RrugcStage2SkillRegistryModel]:
    catalog = list_stage2_skill_catalog(refresh=refresh)
    seen: set[tuple[str, str]] = set()
    for item in catalog.items:
        versions: tuple[str, ...] | None = None
        if refresh and item.source == "openai" and item.skill_id:
            try:
                versions = list_openai_stage2_skill_versions(item.skill_id)
            except Stage2SkillRegistryError:
                versions = item.version_options
        _upsert_catalog_item(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            item=item,
            versions=versions,
        )
        seen.add(_registry_key(item.source, item.skill_name))

    rows = list(
        session.scalars(
            select(RrugcStage2SkillRegistryModel)
            .where(RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
                   RrugcStage2SkillRegistryModel.deleted_at.is_(None))
            .order_by(
                RrugcStage2SkillRegistryModel.enabled.desc(),
                RrugcStage2SkillRegistryModel.display_name,
            )
        )
    )
    for row in rows:
        if row.deleted_at is not None:
            continue
        if _registry_key(row.source, row.skill_name) in seen:
            continue
        row.validation_status = "missing"
        row.sync_state = "missing"
        row.last_error = "Skill is no longer present in the configured source."
        row.updated_at = utcnow()
    session.commit()
    return rows


def ensure_skill_registry(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str | None = None,
    refresh: bool = False,
) -> list[RrugcStage2SkillRegistryModel]:
    rows = list(
        session.scalars(
            select(RrugcStage2SkillRegistryModel)
            .where(RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
                   RrugcStage2SkillRegistryModel.deleted_at.is_(None))
            .order_by(
                RrugcStage2SkillRegistryModel.enabled.desc(),
                RrugcStage2SkillRegistryModel.display_name,
            )
        )
    )
    if refresh or not rows:
        return reconcile_skill_registry(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            refresh=True,
        )
    return rows


def registry_versions(
    session: Session,
    *,
    tenant_id: str,
    registry_id: str,
) -> list[RrugcStage2SkillVersionModel]:
    return list(
        session.scalars(
            select(RrugcStage2SkillVersionModel)
            .where(
                RrugcStage2SkillVersionModel.tenant_id == tenant_id,
                RrugcStage2SkillVersionModel.registry_id == registry_id,
            )
            .order_by(RrugcStage2SkillVersionModel.created_at.desc())
        )
    )


def registry_payload(
    session: Session,
    row: RrugcStage2SkillRegistryModel,
) -> dict[str, Any]:
    versions = registry_versions(
        session,
        tenant_id=row.tenant_id,
        registry_id=row.id,
    )
    return {
        "id": row.id,
        "source": row.source,
        "skill_id": row.skill_id,
        "skill_name": row.skill_name,
        "display_name": row.display_name,
        "description": row.description,
        "note": row.note,
        "workflow": row.workflow,
        "enabled": bool(row.enabled),
        "default_version": row.default_version,
        "latest_version": row.latest_version,
        "synced_version": row.synced_version,
        "sync_state": row.sync_state,
        "validation_status": row.validation_status,
        "bundle_sha256": row.bundle_sha256,
        "last_error": row.last_error,
        "versions": [
            {
                "id": version.id,
                "version": version.version,
                "is_default": bool(version.is_default),
                "is_synced": bool(version.is_synced),
                "status": version.status,
                "bundle_sha256": version.bundle_sha256,
                "created_at": version.created_at,
            }
            for version in versions
        ],
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def enabled_catalog_items(
    session: Session,
    *,
    tenant_id: str,
    catalog_items: tuple[Stage2SkillItem, ...],
) -> tuple[Stage2SkillItem, ...]:
    rows = ensure_skill_registry(session, tenant_id=tenant_id)
    controls = {
        _registry_key(row.source, row.skill_name): row
        for row in rows
    }
    return tuple(
        item
        for item in catalog_items
        if (
            (control := controls.get(_registry_key(item.source, item.skill_name))) is not None
            and control.enabled
            and item.ready
            and control.validation_status not in {"missing", "conflict"}
        )
    )


def assert_skill_enabled(
    session: Session,
    *,
    tenant_id: str,
    source: str,
    skill_id: str | None,
    skill_name: str,
) -> RrugcStage2SkillRegistryModel:
    query = select(RrugcStage2SkillRegistryModel).where(
        RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
        RrugcStage2SkillRegistryModel.source == source,
    )
    if skill_id:
        query = query.where(RrugcStage2SkillRegistryModel.skill_id == skill_id)
    else:
        query = query.where(RrugcStage2SkillRegistryModel.skill_name == skill_name)
    row = session.scalar(query)
    if row is None or not row.enabled:
        raise Stage2SkillRegistryError(
            "stage2_skill_disabled",
            "The selected Stage 2 skill is disabled.",
            status_code=409,
        )
    return row


def create_skill(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    bundle_bytes: bytes,
) -> RrugcStage2SkillRegistryModel:
    bundle = inspect_uploaded_stage2_skill_bundle(bundle_bytes)
    existing = session.scalar(
        select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
            RrugcStage2SkillRegistryModel.skill_name == bundle.skill_name,
        )
    )
    if existing is not None:
        raise Stage2SkillRegistryError(
            "stage2_skill_already_exists",
            "A Stage 2 skill with this name already exists.",
            status_code=409,
        )

    # Uploading through CAM creates a project-local runtime skill. This is
    # intentionally independent of OPENAI_API_KEY; OpenAI-hosted skills remain
    # an optional discovery/sync source managed by the separate refresh flow.
    item = install_local_stage2_skill_bundle(bundle)
    row = _upsert_catalog_item(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        item=item,
        versions=item.version_options,
    )
    row.bundle_sha256 = bundle.bundle_sha256
    row.created_by_user_id = actor_id
    row.updated_by_user_id = actor_id
    _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="stage2_skill.created",
        detail={
            "registry_id": row.id,
            "source": row.source,
            "skill_id": row.skill_id,
            "skill_name": row.skill_name,
            "bundle_sha256": bundle.bundle_sha256,
        },
    )
    session.commit()
    return row


def create_skill_version(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    registry_id: str,
    bundle_bytes: bytes,
    make_default: bool = False,
) -> RrugcStage2SkillRegistryModel:
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    if row.source != "openai" or not row.skill_id:
        raise Stage2SkillRegistryError(
            "stage2_skill_version_unsupported",
            "New versions can only be created for OpenAI-hosted skills.",
            status_code=409,
        )
    bundle = inspect_uploaded_stage2_skill_bundle(bundle_bytes)
    if bundle.skill_name != row.skill_name:
        raise Stage2SkillRegistryError(
            "stage2_skill_name_mismatch",
            "The uploaded version belongs to a different skill.",
            status_code=422,
        )
    version = create_openai_stage2_skill_version(
        row.skill_id,
        bundle,
        make_default=make_default,
    )
    reconcile_skill_registry(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        refresh=True,
    )
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    version_row = _version_row(
        session,
        tenant_id=tenant_id,
        registry_id=registry_id,
        version=version,
    )
    if version_row is not None:
        version_row.bundle_sha256 = bundle.bundle_sha256
    _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="stage2_skill.version_created",
        detail={
            "registry_id": row.id,
            "skill_id": row.skill_id,
            "skill_name": row.skill_name,
            "version": version,
            "make_default": bool(make_default),
            "bundle_sha256": bundle.bundle_sha256,
        },
    )
    session.commit()
    return row


def get_registry_row_by_skill_id(
    session: Session,
    *,
    tenant_id: str,
    skill_id: str,
) -> RrugcStage2SkillRegistryModel:
    row = session.scalar(
        select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
            RrugcStage2SkillRegistryModel.skill_id == skill_id,
        )
    )
    if row is None:
        reconcile_skill_registry(
            session,
            tenant_id=tenant_id,
            refresh=True,
        )
        row = session.scalar(
            select(RrugcStage2SkillRegistryModel).where(
                RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
                RrugcStage2SkillRegistryModel.skill_id == skill_id,
            )
        )
    if row is None:
        raise Stage2SkillRegistryError(
            "stage2_skill_registry_not_found",
            "Stage 2 skill was not found.",
            status_code=404,
        )
    return row


def get_registry_row(
    session: Session,
    *,
    tenant_id: str,
    registry_id: str,
) -> RrugcStage2SkillRegistryModel:
    row = session.scalar(
        select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
            RrugcStage2SkillRegistryModel.id == registry_id,
        )
    )
    if row is None or row.deleted_at is not None:
        raise Stage2SkillRegistryError(
            "stage2_skill_registry_not_found",
            "Stage 2 skill was not found.",
            status_code=404,
        )
    return row


def set_skill_note(
    session: Session, *, tenant_id: str, actor_id: str,
    registry_id: str, note: str,
) -> RrugcStage2SkillRegistryModel:
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    if len(note) > 1500:
        raise Stage2SkillRegistryError("stage2_skill_note_too_long", "Skill note exceeds 1500 characters.", status_code=422)
    row.note = note.strip()
    row.updated_by_user_id = actor_id
    row.updated_at = utcnow()
    _audit(session, tenant_id=tenant_id, actor_id=actor_id,
           action="stage2_skill.note_updated", detail={"registry_id": row.id})
    session.commit()
    return row


def set_skill_enabled(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    registry_id: str,
    enabled: bool,
) -> RrugcStage2SkillRegistryModel:
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    row.enabled = bool(enabled)
    row.updated_by_user_id = actor_id
    row.updated_at = utcnow()
    _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="stage2_skill.enabled" if enabled else "stage2_skill.disabled",
        detail={
            "registry_id": row.id,
            "skill_id": row.skill_id,
            "skill_name": row.skill_name,
        },
    )
    session.commit()
    return row


def set_skill_default(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    registry_id: str,
    version: str,
) -> RrugcStage2SkillRegistryModel:
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    if row.source != "openai" or not row.skill_id:
        raise Stage2SkillRegistryError(
            "stage2_skill_default_unsupported",
            "Default version can only be changed for OpenAI-hosted skills.",
            status_code=409,
        )
    version_row = _version_row(
        session,
        tenant_id=tenant_id,
        registry_id=registry_id,
        version=version,
    )
    if version_row is None or version_row.status != "available":
        raise Stage2SkillRegistryError(
            "stage2_skill_version_not_found",
            "The selected skill version is not available.",
            status_code=404,
        )
    set_openai_stage2_skill_default(row.skill_id, version)
    reconcile_skill_registry(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        refresh=True,
    )
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="stage2_skill.default_version_changed",
        detail={
            "registry_id": row.id,
            "skill_id": row.skill_id,
            "skill_name": row.skill_name,
            "version": version,
        },
    )
    session.commit()
    return row


def sync_skill(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    registry_id: str,
    version: str | None,
) -> RrugcStage2SkillRegistryModel:
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    if row.source != "openai" or not row.skill_id:
        raise Stage2SkillRegistryError(
            "stage2_skill_sync_unsupported",
            "Local skills do not require OpenAI sync.",
            status_code=409,
        )
    active_jobs = int(
        session.scalar(
            select(func.count())
            .select_from(RrugcStage2JobModel)
            .where(
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.skill_source == "openai",
                RrugcStage2JobModel.skill_id == row.skill_id,
                RrugcStage2JobModel.status.in_(("queued", "running")),
            )
        )
        or 0
    )
    if active_jobs:
        raise Stage2SkillRegistryError(
            "stage2_skill_sync_blocked_by_active_jobs",
            "Wait for active jobs using this skill to finish before syncing another version.",
            status_code=409,
        )
    item = sync_openai_stage2_skill(row.skill_id, version=version)
    row.default_version = item.default_version
    row.latest_version = item.latest_version
    row.synced_version = item.synced_version
    row.sync_state = item.sync_state
    row.validation_status = "valid"
    row.bundle_sha256 = installed_stage2_skill_sha256(item.skill_name)
    row.last_error = None
    row.updated_by_user_id = actor_id
    row.updated_at = utcnow()
    versions = list_openai_stage2_skill_versions(row.skill_id)
    _sync_versions(session, row=row, versions=versions)
    _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="stage2_skill.synced",
        detail={
            "registry_id": row.id,
            "skill_id": row.skill_id,
            "skill_name": row.skill_name,
            "version": row.synced_version,
            "bundle_sha256": row.bundle_sha256,
        },
    )
    session.commit()
    return row


def delete_skill_version(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    registry_id: str,
    version: str,
) -> RrugcStage2SkillRegistryModel:
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    if row.source != "openai" or not row.skill_id:
        raise Stage2SkillRegistryError(
            "stage2_skill_version_delete_unsupported",
            "Local skill versions cannot be deleted through this registry.",
            status_code=409,
        )
    if row.synced_version == version:
        raise Stage2SkillRegistryError(
            "stage2_skill_version_is_synced",
            "Sync another version before deleting the currently installed version.",
            status_code=409,
        )
    active_jobs = int(
        session.scalar(
            select(func.count())
            .select_from(RrugcStage2JobModel)
            .where(
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.skill_id == row.skill_id,
                RrugcStage2JobModel.skill_version == version,
                RrugcStage2JobModel.status.in_(("queued", "running")),
            )
        )
        or 0
    )
    if active_jobs:
        raise Stage2SkillRegistryError(
            "stage2_skill_version_in_use",
            "This version is pinned by active Stage 2 jobs.",
            status_code=409,
        )
    delete_openai_stage2_skill_version(row.skill_id, version)
    reconcile_skill_registry(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        refresh=True,
    )
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="stage2_skill.version_deleted",
        detail={
            "registry_id": row.id,
            "skill_id": row.skill_id,
            "skill_name": row.skill_name,
            "version": version,
        },
    )
    session.commit()
    return row


def delete_skill(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    registry_id: str,
) -> None:
    row = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
    active_jobs = int(
        session.scalar(
            select(func.count())
            .select_from(RrugcStage2JobModel)
            .where(
                RrugcStage2JobModel.tenant_id == tenant_id,
                RrugcStage2JobModel.skill_name == row.skill_name,
                RrugcStage2JobModel.status.in_(("queued", "running")),
            )
        )
        or 0
    )
    for model in (RrugcKeywordImageJobModel, RrugcColorwayJobModel):
        active_jobs += int(session.scalar(
            select(func.count()).select_from(model).where(
                model.tenant_id == tenant_id,
                model.skill_source == row.source,
                model.skill_name == row.skill_name,
                model.status.in_(("queued", "running")),
            )
        ) or 0)
    if active_jobs:
        raise Stage2SkillRegistryError(
            "stage2_skill_delete_blocked_by_active_jobs",
            "Wait for active jobs using this skill to finish before deleting it.",
            status_code=409,
        )
    if row.source == "local":
        # Keep project-bundled runtime files intact. Tombstone prevents rediscovery
        # and blocks future jobs, while historical jobs and their outputs remain.
        row.enabled = False
        row.deleted_at = utcnow()
        row.updated_by_user_id = actor_id
        _audit(session, tenant_id=tenant_id, actor_id=actor_id,
               action="stage2_skill.deleted", detail={"registry_id": row.id,
               "skill_name": row.skill_name, "mode": "registry_archive"})
        session.commit()
        return
    skill_id = row.skill_id
    skill_name = row.skill_name
    delete_openai_stage2_skill(skill_id)
    session.execute(
        delete(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
            RrugcStage2SkillRegistryModel.id == registry_id,
        )
    )
    _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="stage2_skill.deleted",
        detail={
            "registry_id": registry_id,
            "skill_id": skill_id,
            "skill_name": skill_name,
        },
    )
    session.commit()

def restore_keyword_skill(
    session: Session, *, tenant_id: str, actor_id: str, registry_id: str,
) -> RrugcStage2SkillRegistryModel:
    """Admin-only recovery for archived local keyword-only skills.

    Recovery is explicit, audited and never occurs during automatic catalog scans.
    Stage 2/4 defaults and all saved generation outputs are unaffected.
    """
    from app.modules.realistic_review_ugc.stage_skill_settings import keyword_skill_compatible

    row = session.scalar(
        select(RrugcStage2SkillRegistryModel)
        .where(
            RrugcStage2SkillRegistryModel.id == registry_id,
            RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
        ).with_for_update()
    )
    if row is None or row.source != "local":
        raise Stage2SkillRegistryError(
            "stage_skill_restore_not_found", "Archived local skill not found.", status_code=404,
        )
    if row.deleted_at is None:
        raise Stage2SkillRegistryError(
            "stage_skill_not_archived", "This skill is not archived.", status_code=409,
        )
    catalog = list_stage2_skill_catalog(refresh=False)
    if not any(item.source == row.source and item.skill_name == row.skill_name and item.ready
               for item in catalog.items):
        raise Stage2SkillRegistryError(
            "stage_skill_restore_runtime_missing", "Install or sync the local skill before restoring it.",
            status_code=409,
        )
    if not keyword_skill_compatible(row.skill_name):
        raise Stage2SkillRegistryError(
            "stage_skill_restore_incompatible",
            "This skill cannot generate Stage 1 keyword artwork without source images.",
            status_code=422,
        )
    row.deleted_at = None
    row.enabled = True
    row.validation_status = "valid"
    row.sync_state = "ready"
    row.updated_by_user_id = actor_id
    row.updated_at = utcnow()
    default = session.get(RrugcStageSkillDefaultModel, (tenant_id, "stage1"))
    if default is None:
        default = RrugcStageSkillDefaultModel(tenant_id=tenant_id, stage="stage1")
        session.add(default)
    default.registry_id = row.id
    default.updated_by_user_id = actor_id
    default.updated_at = utcnow()
    _audit(session, tenant_id=tenant_id, actor_id=actor_id,
           action="stage2_skill.restored",
           detail={"registry_id": row.id, "skill_name": row.skill_name,
                   "stage": "stage1", "restored_from_archive": True})
    _audit(session, tenant_id=tenant_id, actor_id=actor_id,
           action="rrugc.stage_skill.default_updated",
           detail={"stage": "stage1", "registry_id": row.id,
                   "skill_name": row.skill_name})
    session.commit()
    return row
