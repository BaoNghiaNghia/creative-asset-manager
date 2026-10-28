"""Tenant-safe read/write gateway for the Typography reference metadata.

All reads include tenant AND effective visibility; an existing UGC reference
asset may back a typography item without changing the asset's original profile.
Never access by an unscoped reference id, even for privileged administrators.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel
from app.modules.realistic_review_ugc.typography_model import RrugcTypographyReferenceModel, RrugcTypographyStyleDnaModel


class TypographyReferenceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TypographyReferenceRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _visible(user_id: str, manage: bool):
        if manage:
            return True
        return or_(
            RrugcTypographyReferenceModel.privacy == "workspace",
            RrugcTypographyReferenceModel.created_by_user_id == user_id,
        )

    def get(self, tenant_id: str, ref_id: str, *, user_id: str, manage: bool = False):
        return self.session.scalar(
            select(RrugcTypographyReferenceModel).where(
                RrugcTypographyReferenceModel.id == ref_id,
                RrugcTypographyReferenceModel.tenant_id == tenant_id,
                self._visible(user_id, manage),
            )
        )

    def by_hash(self, tenant_id: str, sha256: str, *, user_id: str, manage: bool = False):
        return self.session.scalar(
            select(RrugcTypographyReferenceModel).where(
                RrugcTypographyReferenceModel.tenant_id == tenant_id,
                RrugcTypographyReferenceModel.content_hash == sha256,
                self._visible(user_id, manage),
            )
        )

    def list(
        self, tenant_id: str, *, user_id: str, manage: bool = False,
        status: str | None = None, query: str | None = None,
        limit: int = 40, offset: int = 0,
    ) -> tuple[list[RrugcTypographyReferenceModel], int]:
        conditions = [
            RrugcTypographyReferenceModel.tenant_id == tenant_id,
            self._visible(user_id, manage),
        ]
        if status:
            conditions.append(RrugcTypographyReferenceModel.editorial_status == status)
        else:
            conditions.append(RrugcTypographyReferenceModel.editorial_status != "archived")
        if query:
            # Plain keyword search. User-provided % and _ are escaped so a
            # search cannot silently expand to an all-tenant scan.
            escaped = query[:100].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = "%" + escaped + "%"
            conditions.append(
                or_(
                    RrugcTypographyReferenceModel.notes.ilike(pattern, escape="\\"),
                    RrugcTypographyReferenceModel.source_attribution.ilike(pattern, escape="\\"),
                    RrugcTypographyReferenceModel.skill_name.ilike(pattern, escape="\\"),
                )
            )
        statement = select(RrugcTypographyReferenceModel).where(*conditions)
        total = self.session.scalar(
            select(func.count()).select_from(RrugcTypographyReferenceModel).where(*conditions)
        ) or 0
        rows = list(self.session.scalars(
            statement.order_by(
                RrugcTypographyReferenceModel.updated_at.desc(),
                RrugcTypographyReferenceModel.id.desc(),
            ).offset(offset).limit(limit)
        ))
        return rows, total

    def link_asset(
        self, *, tenant_id: str, workspace_id: str, asset_id: str,
        user_id: str, rights: str = "unknown", privacy: str = "private",
        attribution: str | None = None, source_type: str = "upload",
    ) -> tuple[RrugcTypographyReferenceModel, bool]:
        """Link an already stored asset; caller owns transaction/commit.

        This checks tenant+asset identity AND content hash, and performs no
        storage upload, UGC profile mutation, or automatic rights approval.
        """
        asset = self.session.scalar(
            select(RrugcReferenceAssetModel).where(
                RrugcReferenceAssetModel.tenant_id == tenant_id,
                RrugcReferenceAssetModel.id == asset_id,
                RrugcReferenceAssetModel.status == "ready",
            )
        )
        if asset is None:
            raise TypographyReferenceError("reference_asset_not_found", "Ready reference asset was not found")
        existing = self.session.scalar(
            select(RrugcTypographyReferenceModel).where(
                RrugcTypographyReferenceModel.tenant_id == tenant_id,
                RrugcTypographyReferenceModel.content_hash == asset.content_hash,
            )
        )
        if existing is not None:
            # Deliberately do not disclose another owner's private link.
            if existing.privacy == "private" and existing.created_by_user_id != user_id:
                raise TypographyReferenceError("reference_already_private", "This image already exists as a private typography reference")
            return existing, False
        if rights not in {"owned", "licensed", "permitted_reference", "unknown", "blocked"}:
            raise TypographyReferenceError("reference_rights_invalid", "Reference rights status is invalid")
        if privacy not in {"private", "workspace"}:
            raise TypographyReferenceError("reference_privacy_invalid", "Reference privacy is invalid")
        if source_type not in {"upload", "scout", "internal"}:
            raise TypographyReferenceError("reference_source_invalid", "Reference source type is invalid")
        row = RrugcTypographyReferenceModel(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            asset_id=asset.id,
            content_hash=asset.content_hash,
            source_type=source_type,
            source_rights_status=rights,
            source_attribution=attribution,
            privacy=privacy,
            created_by_user_id=user_id,
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    def update_editorial(
        self, tenant_id: str, ref_id: str, *,
        user_id: str, status: str, rights: str | None = None,
    ) -> RrugcTypographyReferenceModel:
        row = self.get(tenant_id, ref_id, user_id=user_id, manage=True)
        if row is None:
            raise TypographyReferenceError("reference_not_found", "Typography reference not found")
        if status not in {"candidate", "approved", "rejected", "archived"}:
            raise TypographyReferenceError("reference_status_invalid", "Reference status is invalid")
        effective_rights = rights if rights is not None else row.source_rights_status
        if effective_rights not in {"owned", "licensed", "permitted_reference", "unknown", "blocked"}:
            raise TypographyReferenceError("reference_rights_invalid", "Reference rights status is invalid")
        if status == "approved" and effective_rights not in {"owned", "licensed", "permitted_reference"}:
            raise TypographyReferenceError("reference_rights_unverified", "Unknown or blocked rights cannot be approved")
        row.editorial_status = status
        row.source_rights_status = effective_rights
        row.reviewed_by_user_id = user_id
        return row

    def dna_versions(self, tenant_id: str, ref_id: str, *, user_id: str, manage: bool = False):
        if self.get(tenant_id, ref_id, user_id=user_id, manage=manage) is None:
            return []
        return list(self.session.scalars(
            select(RrugcTypographyStyleDnaModel).where(
                RrugcTypographyStyleDnaModel.tenant_id == tenant_id,
                RrugcTypographyStyleDnaModel.reference_id == ref_id,
            ).order_by(RrugcTypographyStyleDnaModel.analyzed_at.desc())
        ))
