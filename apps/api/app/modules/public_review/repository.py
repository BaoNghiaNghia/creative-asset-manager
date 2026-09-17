from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.public_review.model import AssetAnnotationModel, PublicShareGuestModel, PublicShareModel, PublicShareScopeModel, PublicShareSessionModel, utcnow
from app.modules.public_review.schema import extract_plain_text, validate_annotation_document


class PublicReviewRepository:
    """Tenant-explicit persistence primitives; callers own transaction boundaries."""

    def __init__(self, session: Session):
        self.session = session

    def create_share(self, **values) -> PublicShareModel:
        row = PublicShareModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def get_share(self, tenant_id: str, share_id: str) -> PublicShareModel | None:
        return self.session.scalar(select(PublicShareModel).where(PublicShareModel.tenant_id == tenant_id, PublicShareModel.id == share_id))

    def get_share_by_public_id(self, public_id: str) -> PublicShareModel | None:
        return self.session.scalar(select(PublicShareModel).where(PublicShareModel.public_id == public_id))

    def get_share_by_secret_digest(self, public_id: str, secret_digest: str) -> PublicShareModel | None:
        return self.session.scalar(select(PublicShareModel).where(PublicShareModel.public_id == public_id, PublicShareModel.secret_digest == secret_digest))

    def list_shares(self, tenant_id: str) -> list[PublicShareModel]:
        return list(self.session.scalars(select(PublicShareModel).where(PublicShareModel.tenant_id == tenant_id).order_by(PublicShareModel.created_at.desc())))

    def update_share(self, tenant_id: str, share_id: str, **values) -> PublicShareModel:
        row = self._required_share(tenant_id, share_id)
        for field in {"name", "allow_comments", "allow_download", "expires_at", "secret_digest"} & values.keys():
            setattr(row, field, values[field])
        self.session.flush()
        return row

    def revoke_share(self, tenant_id: str, share_id: str, now: datetime | None = None) -> PublicShareModel:
        row = self._required_share(tenant_id, share_id)
        if row.status != "revoked":
            row.status, row.revoked_at = "revoked", now or utcnow()
            self.revoke_sessions(tenant_id, share_id, row.revoked_at)
        self.session.flush()
        return row

    def replace_scopes(self, tenant_id: str, share_id: str, scopes: list[dict]) -> list[PublicShareScopeModel]:
        self._required_share(tenant_id, share_id)
        unique = {(item["external_source_id"], item["folder_external_id"]) for item in scopes}
        if len(unique) != len(scopes):
            raise ValueError("duplicate share scope")
        for source_id, folder_id in unique:
            if self.session.scalar(select(ExternalSourceModel.id).where(ExternalSourceModel.tenant_id == tenant_id, ExternalSourceModel.id == source_id)) is None:
                raise LookupError("external source is outside the share tenant")
        for row in self.session.scalars(select(PublicShareScopeModel).where(PublicShareScopeModel.tenant_id == tenant_id, PublicShareScopeModel.share_id == share_id)):
            self.session.delete(row)
        rows = [PublicShareScopeModel(tenant_id=tenant_id, share_id=share_id, **item) for item in scopes]
        self.session.add_all(rows)
        self.session.flush()
        return rows

    def validate_management_scopes(self, tenant_id: str, scopes: list[dict]) -> None:
        unique = {(item["external_source_id"], item["folder_external_id"]) for item in scopes}
        if len(unique) != len(scopes):
            raise ValueError("duplicate share scope")
        for source_id, folder_id in unique:
            source = self.session.scalar(select(ExternalSourceModel.id).where(ExternalSourceModel.tenant_id == tenant_id, ExternalSourceModel.id == source_id))
            if source is None:
                raise LookupError("external source is outside the share tenant")
            folder = self.session.scalar(select(SourceAssetModel.id).where(SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.external_source_id == source_id, SourceAssetModel.external_asset_id == folder_id, SourceAssetModel.deleted_at.is_(None)))
            if folder is None:
                raise LookupError("folder is not available in the selected source")

    def list_scopes(self, tenant_id: str, share_id: str) -> list[PublicShareScopeModel]:
        return list(self.session.scalars(select(PublicShareScopeModel).where(PublicShareScopeModel.tenant_id == tenant_id, PublicShareScopeModel.share_id == share_id).order_by(PublicShareScopeModel.created_at)))

    def create_guest(self, **values) -> PublicShareGuestModel:
        self._required_share(values["tenant_id"], values["share_id"])
        row = PublicShareGuestModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def get_guest(self, tenant_id: str, share_id: str, guest_id: str) -> PublicShareGuestModel | None:
        return self.session.scalar(select(PublicShareGuestModel).where(PublicShareGuestModel.tenant_id == tenant_id, PublicShareGuestModel.share_id == share_id, PublicShareGuestModel.id == guest_id))

    def update_guest(self, tenant_id: str, share_id: str, guest_id: str, display_name: str) -> PublicShareGuestModel:
        row = self._required_guest(tenant_id, share_id, guest_id)
        row.display_name, row.last_seen_at = display_name, utcnow()
        self.session.flush()
        return row

    def create_session(self, **values) -> PublicShareSessionModel:
        self._required_share(values["tenant_id"], values["share_id"])
        if values.get("guest_id") is not None:
            self._required_guest(values["tenant_id"], values["share_id"], values["guest_id"])
        row = PublicShareSessionModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def get_session_by_digest(self, tenant_id: str, session_digest: str) -> PublicShareSessionModel | None:
        return self.session.scalar(select(PublicShareSessionModel).where(PublicShareSessionModel.tenant_id == tenant_id, PublicShareSessionModel.session_digest == session_digest))

    def revoke_session(self, tenant_id: str, share_id: str, session_id: str, now: datetime | None = None) -> PublicShareSessionModel:
        row = self.session.scalar(select(PublicShareSessionModel).where(PublicShareSessionModel.tenant_id == tenant_id, PublicShareSessionModel.share_id == share_id, PublicShareSessionModel.id == session_id))
        if row is None:
            raise LookupError("public share session not found")
        row.revoked_at = now or utcnow()
        self.session.flush()
        return row

    def revoke_sessions(self, tenant_id: str, share_id: str, now: datetime | None = None) -> None:
        timestamp = now or utcnow()
        for row in self.session.scalars(select(PublicShareSessionModel).where(PublicShareSessionModel.tenant_id == tenant_id, PublicShareSessionModel.share_id == share_id, PublicShareSessionModel.revoked_at.is_(None))):
            row.revoked_at = timestamp

    def audit_share_event(self, *, tenant_id: str, actor_id: str, action: str, share_id: str, detail: dict | None = None) -> None:
        self.session.add(AuthAuditEventModel(tenant_id=tenant_id, actor_id=actor_id, action=action, detail_json={"share_id": share_id, **dict(detail or {})}))
        self.session.flush()

    def create_annotation(self, **values) -> AssetAnnotationModel:
        self._validate_annotation_context(**values)
        content_json = validate_annotation_document(values["content_json"])
        values["plain_text"] = extract_plain_text(content_json)
        row = AssetAnnotationModel(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def list_annotations(self, tenant_id: str, share_id: str, asset_id: str, source_asset_id: str) -> list[AssetAnnotationModel]:
        return list(self.session.scalars(select(AssetAnnotationModel).where(AssetAnnotationModel.tenant_id == tenant_id, AssetAnnotationModel.share_id == share_id, AssetAnnotationModel.asset_id == asset_id, AssetAnnotationModel.source_asset_id == source_asset_id).order_by(AssetAnnotationModel.created_at)))

    def update_annotation(self, tenant_id: str, share_id: str, annotation_id: str, **values) -> AssetAnnotationModel:
        row = self._required_annotation(tenant_id, share_id, annotation_id)
        if "content_json" in values:
            row.content_json = validate_annotation_document(values["content_json"])
            row.plain_text = extract_plain_text(row.content_json)
        for field in {"anchor_x", "anchor_y"} & values.keys():
            setattr(row, field, values[field])
        row.edited_at = utcnow()
        self.session.flush()
        return row

    def delete_annotation(self, tenant_id: str, share_id: str, annotation_id: str) -> None:
        self.session.delete(self._required_annotation(tenant_id, share_id, annotation_id))
        self.session.flush()

    def resolve_annotation(self, tenant_id: str, share_id: str, annotation_id: str, resolved_by: str, now: datetime | None = None) -> AssetAnnotationModel:
        row = self._required_annotation(tenant_id, share_id, annotation_id)
        row.status, row.resolved_at, row.resolved_by = "resolved", now or utcnow(), resolved_by
        self.session.flush()
        return row

    def _validate_annotation_context(self, *, tenant_id: str, share_id: str, asset_id: str, source_asset_id: str, guest_id: str, parent_annotation_id: str | None = None, **_values) -> None:
        self._required_share(tenant_id, share_id)
        self._required_guest(tenant_id, share_id, guest_id)
        asset = self.session.scalar(select(AssetModel.id).where(AssetModel.tenant_id == tenant_id, AssetModel.id == asset_id))
        source = self.session.scalar(select(SourceAssetModel.id).where(SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.id == source_asset_id))
        link = self.session.scalar(select(AssetSourceLinkModel.id).where(AssetSourceLinkModel.tenant_id == tenant_id, AssetSourceLinkModel.asset_id == asset_id, AssetSourceLinkModel.source_asset_id == source_asset_id))
        if asset is None or source is None or link is None:
            raise LookupError("annotation asset/source pair is outside the tenant")
        if parent_annotation_id is not None:
            parent = self._required_annotation(tenant_id, share_id, parent_annotation_id)
            if parent.asset_id != asset_id or parent.source_asset_id != source_asset_id:
                raise ValueError("annotation reply must remain in the same asset/source thread")

    def _required_share(self, tenant_id: str, share_id: str) -> PublicShareModel:
        row = self.get_share(tenant_id, share_id)
        if row is None:
            raise LookupError("public share not found")
        return row

    def _required_guest(self, tenant_id: str, share_id: str, guest_id: str) -> PublicShareGuestModel:
        row = self.get_guest(tenant_id, share_id, guest_id)
        if row is None:
            raise LookupError("public share guest not found")
        return row

    def _required_annotation(self, tenant_id: str, share_id: str, annotation_id: str) -> AssetAnnotationModel:
        row = self.session.scalar(select(AssetAnnotationModel).where(AssetAnnotationModel.tenant_id == tenant_id, AssetAnnotationModel.share_id == share_id, AssetAnnotationModel.id == annotation_id))
        if row is None:
            raise LookupError("asset annotation not found")
        return row

    def list_annotations_with_guests(self, tenant_id: str, share_id: str, asset_id: str, source_asset_id: str):
        return list(self.session.execute(select(AssetAnnotationModel, PublicShareGuestModel.display_name).join(PublicShareGuestModel, (PublicShareGuestModel.tenant_id == AssetAnnotationModel.tenant_id) & (PublicShareGuestModel.share_id == AssetAnnotationModel.share_id) & (PublicShareGuestModel.id == AssetAnnotationModel.guest_id)).where(AssetAnnotationModel.tenant_id == tenant_id, AssetAnnotationModel.share_id == share_id, AssetAnnotationModel.asset_id == asset_id, AssetAnnotationModel.source_asset_id == source_asset_id).order_by(AssetAnnotationModel.created_at)))
