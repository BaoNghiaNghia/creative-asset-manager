from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.database import Base
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel
from app.modules.authorization.principal import is_pure_viewer
from app.modules.authorization.folder_scope_cache import (
    ParentMap,
    ViewerFolderHierarchyCache,
    viewer_folder_hierarchy_cache,
)


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
class FolderScopeAccess:
    """The provider-folder boundary for a caller with an already authenticated identity."""

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


ViewerFolderAccess = FolderScopeAccess


class FolderScopeResolver:
    """Resolve scoped provider folders through synchronized, tenant-local ancestry."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _parents_from_metadata(metadata: object) -> tuple[str, ...]:
        values = metadata if isinstance(metadata, dict) else {}
        raw = values.get("parents")
        if not isinstance(raw, list):
            parent = values.get("parent_id")
            raw = [parent] if parent else []
        return tuple(str(value) for value in raw if value)

    def _load_parent_map(self, *, tenant_id: str, external_source_id: str) -> ParentMap:
        rows = self.session.execute(
            select(SourceAssetModel.external_asset_id, SourceAssetModel.source_metadata).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id == external_source_id,
                SourceAssetModel.deleted_at.is_(None),
            )
        ).all()
        return {
            str(external_asset_id): self._parents_from_metadata(metadata)
            for external_asset_id, metadata in rows
        }

    def parent_map(self, *, tenant_id: str, external_source_id: str) -> ParentMap | None:
        return viewer_folder_hierarchy_cache.get_or_load(
            tenant_id=tenant_id,
            external_source_id=external_source_id,
            loader=lambda: self._load_parent_map(
                tenant_id=tenant_id, external_source_id=external_source_id,
            ),
        )

    def allowed_asset_source_pairs(self, *, tenant_id: str, access: FolderScopeAccess) -> set[tuple[str, str]]:
        if not access.restricted or not access.source_id:
            return set()
        parents_by_external_id = self.parent_map(
            tenant_id=tenant_id, external_source_id=access.source_id,
        )
        if parents_by_external_id is None:
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
        allowed: set[tuple[str, str]] = set()
        for asset_id, source in rows:
            if self._has_selected_ancestor(
                parents_by_external_id,
                str(source.external_asset_id),
                access.folder_ids,
            ):
                allowed.add((str(asset_id), str(source.id)))
        return allowed

    def allowed_source_asset_ids(self, *, tenant_id: str, access: FolderScopeAccess) -> set[str]:
        if not access.restricted or not access.source_id:
            return set()
        parents_by_external_id = self.parent_map(
            tenant_id=tenant_id,
            external_source_id=access.source_id,
        )
        if parents_by_external_id is None:
            return set()
        rows = self.session.execute(
            select(SourceAssetModel.id, SourceAssetModel.external_asset_id).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id == access.source_id,
                SourceAssetModel.deleted_at.is_(None),
            )
        ).all()
        return {
            str(source_asset_id)
            for source_asset_id, external_asset_id in rows
            if self._has_selected_ancestor(
                parents_by_external_id, str(external_asset_id), access.folder_ids,
            )
        }

    def allows_external_asset(self, *, tenant_id: str, access: FolderScopeAccess, external_asset_id: str) -> bool:
        if not access.restricted:
            return True
        if not access.source_id:
            return False
        item_id = str(external_asset_id)
        if item_id in access.folder_ids:
            return True

        # Single-item authorization should not materialize the full source
        # hierarchy. Walk the synchronized parent pointer using indexed source
        # identity lookups and keep the legacy metadata fallback for old rows.
        current = item_id
        visited: set[str] = set()
        for _depth in range(64):
            if current in visited:
                return False
            visited.add(current)
            row = self.session.execute(
                select(SourceAssetModel.parent_external_id, SourceAssetModel.source_metadata).where(
                    SourceAssetModel.tenant_id == tenant_id,
                    SourceAssetModel.external_source_id == access.source_id,
                    SourceAssetModel.external_asset_id == current,
                    SourceAssetModel.deleted_at.is_(None),
                )
            ).one_or_none()
            if row is None:
                return False
            parent_external_id, metadata = row
            parents = (
                (str(parent_external_id),)
                if isinstance(parent_external_id, str) and parent_external_id
                else self._parents_from_metadata(metadata)
            )
            if any(parent in access.folder_ids for parent in parents):
                return True
            if not parents:
                return False
            current = parents[0]
        return False

    @staticmethod
    def _has_selected_ancestor(
        parent_map: ParentMap,
        item_id: str,
        selected_folder_ids: frozenset[str],
    ) -> bool:
        pending = list(parent_map.get(item_id, ()))
        visited: set[str] = set()
        while pending:
            parent = pending.pop()
            if parent in visited:
                continue
            visited.add(parent)
            if parent in selected_folder_ids:
                return True
            pending.extend(parent_map.get(parent, ()))
        return False


class ViewerFolderScopeService:
    def __init__(self, session: Session):
        self.session = session
        self._resolver = FolderScopeResolver(session)

    def access(self, *, tenant_id: str, membership_id: str, roles: frozenset[str], external_source_id: str | None) -> ViewerFolderAccess:
        restricted = is_pure_viewer(type("_Principal", (), {"effective_roles": roles})())
        if not restricted:
            return ViewerFolderAccess(False, external_source_id, frozenset())
        if not external_source_id:
            return ViewerFolderAccess(True, None, frozenset())
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

    def allowed_asset_source_pairs(self, *, tenant_id: str, access: ViewerFolderAccess) -> set[tuple[str, str]]:
        return self._resolver.allowed_asset_source_pairs(tenant_id=tenant_id, access=access)

    def allowed_source_asset_ids(self, *, tenant_id: str, access: ViewerFolderAccess) -> set[str]:
        return self._resolver.allowed_source_asset_ids(tenant_id=tenant_id, access=access)

    def allowed_internal_asset_ids(self, *, tenant_id: str, access: ViewerFolderAccess) -> set[str]:
        return {
            asset_id
            for asset_id, _source_asset_id in self.allowed_asset_source_pairs(
                tenant_id=tenant_id, access=access,
            )
        }

    def allows_external_asset(self, *, tenant_id: str, access: ViewerFolderAccess, external_asset_id: str) -> bool:
        return self._resolver.allows_external_asset(
            tenant_id=tenant_id, access=access, external_asset_id=external_asset_id,
        )

    def allowed_asset_source_pairs_for_membership(
        self, *, tenant_id: str, membership_id: str,
    ) -> set[tuple[str, str]]:
        scopes = self.list_membership_scopes(tenant_id=tenant_id, membership_id=membership_id)
        allowed: set[tuple[str, str]] = set()
        for source_id, folder_ids in scopes.items():
            allowed.update(self.allowed_asset_source_pairs(
                tenant_id=tenant_id,
                access=ViewerFolderAccess(True, source_id, frozenset(folder_ids)),
            ))
        return allowed

    def allowed_internal_asset_ids_for_membership(self, *, tenant_id: str, membership_id: str) -> set[str]:
        return {
            asset_id
            for asset_id, _source_asset_id in self.allowed_asset_source_pairs_for_membership(
                tenant_id=tenant_id, membership_id=membership_id,
            )
        }

    def list_membership_scopes(self, *, tenant_id: str, membership_id: str) -> dict[str, set[str]]:
        rows = self.session.scalars(select(ViewerFolderScopeModel).where(
            ViewerFolderScopeModel.tenant_id == tenant_id,
            ViewerFolderScopeModel.tenant_membership_id == membership_id,
        ))
        result: dict[str, set[str]] = {}
        for row in rows:
            source_id = str(row.external_source_id or "").strip()
            folder_id = str(row.folder_external_id or "").strip()
            if source_id and folder_id:
                result.setdefault(source_id, set()).add(folder_id)
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
