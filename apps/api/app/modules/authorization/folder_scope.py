from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel
from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ViewerFolderScopeModel(Base):
    """A selected external folder visible to a viewer membership.

    External folder IDs are provider IDs, never internal asset IDs.
    """

    __tablename__ = "viewer_folder_scopes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "tenant_membership_id", "external_source_id", "folder_external_id",
            name="uq_viewer_folder_scope",
        ),
        Index("ix_viewer_folder_scope_membership", "tenant_id", "tenant_membership_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    tenant_membership_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenant_memberships.id", ondelete="CASCADE"), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    folder_external_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    folder_name: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


@dataclass(frozen=True, slots=True)
class ViewerFolderAccess:
    restricted: bool
    source_id: str | None
    folder_ids: frozenset[str]

    def allows(self, *, item_id: str, parent_id: str | None = None, ancestor_ids: list[str] | None = None) -> bool:
        if not self.restricted:
            return True
        ids = {str(value) for value in (ancestor_ids or []) if value}
        if parent_id:
            ids.add(str(parent_id))
        return str(item_id) in self.folder_ids or bool(ids.intersection(self.folder_ids))


class ViewerFolderScopeService:
    def __init__(self, session: Session):
        self.session = session

    def access(self, *, tenant_id: str, membership_id: str, roles: frozenset[str], external_source_id: str | None) -> ViewerFolderAccess:
        restricted = "viewer" in roles and not roles.intersection({"operator", "tenant_admin", "billing_admin"})
        if not restricted or not external_source_id:
            return ViewerFolderAccess(False, external_source_id, frozenset())
        ids = self.session.scalars(
            select(ViewerFolderScopeModel.folder_external_id).where(
                ViewerFolderScopeModel.tenant_id == tenant_id,
                ViewerFolderScopeModel.tenant_membership_id == membership_id,
                ViewerFolderScopeModel.external_source_id == external_source_id,
            )
        )
        return ViewerFolderAccess(True, external_source_id, frozenset(ids))

    def list(self, *, tenant_id: str, membership_id: str, external_source_id: str) -> list[ViewerFolderScopeModel]:
        return list(self.session.scalars(select(ViewerFolderScopeModel).where(
            ViewerFolderScopeModel.tenant_id == tenant_id,
            ViewerFolderScopeModel.tenant_membership_id == membership_id,
            ViewerFolderScopeModel.external_source_id == external_source_id,
        ).order_by(ViewerFolderScopeModel.folder_name, ViewerFolderScopeModel.folder_external_id)))

    def allowed_internal_asset_ids(self, *, tenant_id: str, access: ViewerFolderAccess) -> set[str]:
        """Resolve selected external folders to internal assets for index search."""
        if not access.restricted or not access.source_id:
            return set()
        rows = self.session.execute(
            select(AssetSourceLinkModel.asset_id, SourceAssetModel)
            .join(SourceAssetModel, SourceAssetModel.id == AssetSourceLinkModel.source_asset_id)
            .where(
                AssetSourceLinkModel.tenant_id == tenant_id,
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id == access.source_id,
                SourceAssetModel.deleted_at.is_(None),
            )
        ).all()
        allowed: set[str] = set()
        for asset_id, source in rows:
            metadata = source.source_metadata or {}
            parents = metadata.get("parents")
            if not isinstance(parents, list):
                parent = metadata.get("parent_id")
                parents = [parent] if parent else []
            if any(str(parent) in access.folder_ids for parent in parents if parent):
                allowed.add(str(asset_id))
        return allowed

    def allowed_internal_asset_ids_for_membership(self, *, tenant_id: str, membership_id: str) -> set[str]:
        """Resolve every selected source folder for a viewer membership."""
        scopes = self.list_membership_scopes(tenant_id=tenant_id, membership_id=membership_id)
        allowed: set[str] = set()
        for source_id, folder_ids in scopes.items():
            allowed.update(self.allowed_internal_asset_ids(
                tenant_id=tenant_id,
                access=ViewerFolderAccess(True, source_id, frozenset(folder_ids)),
            ))
        return allowed

    def list_membership_scopes(self, *, tenant_id: str, membership_id: str) -> dict[str, set[str]]:
        rows = self.session.scalars(select(ViewerFolderScopeModel).where(
            ViewerFolderScopeModel.tenant_id == tenant_id,
            ViewerFolderScopeModel.tenant_membership_id == membership_id,
        ))
        result: dict[str, set[str]] = {}
        for row in rows:
            result.setdefault(row.external_source_id, set()).add(row.folder_external_id)
        return result

    def replace(self, *, tenant_id: str, membership_id: str, external_source_id: str, folders: list[dict]) -> list[ViewerFolderScopeModel]:
        current = self.list(tenant_id=tenant_id, membership_id=membership_id, external_source_id=external_source_id)
        wanted = {str(item["folder_id"]): str(item.get("folder_name") or "") for item in folders if str(item.get("folder_id") or "")}
        for row in current:
            if row.folder_external_id not in wanted:
                self.session.delete(row)
        existing = {row.folder_external_id: row for row in current}
        for folder_id, name in wanted.items():
            row = existing.get(folder_id)
            if row is None:
                self.session.add(ViewerFolderScopeModel(
                    tenant_id=tenant_id, tenant_membership_id=membership_id,
                    external_source_id=external_source_id, folder_external_id=folder_id,
                    folder_name=name or None,
                ))
            elif name and row.folder_name != name:
                row.folder_name = name
        self.session.flush()
        return self.list(tenant_id=tenant_id, membership_id=membership_id, external_source_id=external_source_id)
