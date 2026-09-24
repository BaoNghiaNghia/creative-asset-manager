from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select

from app.modules.assets.media_types import IMAGE_MIME_BY_EXTENSION, infer_media_type
from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.pipeline.mime_types import is_eligible_image_source_asset


@dataclass(frozen=True)
class CoverageResource:
    source_asset_id: str
    external_source_id: str
    source_type: str
    display_name: str | None
    mime_type: str | None
    asset_id: str | None
    content_hash: str | None
    imported: bool
    eligible: bool
    unsupported: bool
    activity_at: datetime | None = None


class VisualCoverageResourceReader:
    """Read-only, set-based SourceAsset coverage input for visual search."""

    def __init__(self, session):
        self.session = session

    @staticmethod
    def _resource(source, link, asset, external) -> CoverageResource | None:
        if not is_eligible_image_source_asset(source):
            return None
        imported = bool(link and asset and asset.tenant_id == source.tenant_id)
        supported = is_eligible_image_source_asset(source)
        valid_hash = bool(
            asset
            and isinstance(asset.content_hash, str)
            and len(asset.content_hash) == 64
        )
        activity_values = [
            value
            for value in (
                source.source_modified_at,
                source.last_seen_at,
                source.updated_at,
                asset.updated_at if asset is not None else None,
            )
            if value is not None
        ]
        activity_at = max(activity_values) if activity_values else None
        return CoverageResource(
            source.id,
            source.external_source_id,
            external.source_type,
            external.display_name,
            source.mime_type,
            asset.id if imported else None,
            asset.content_hash if imported else None,
            imported,
            imported and supported and valid_hash,
            not supported,
            activity_at,
        )

    def resources(self, tenant_id: str) -> list[CoverageResource]:
        rows = self.session.execute(
            select(
                SourceAssetModel,
                AssetSourceLinkModel,
                AssetModel,
                ExternalSourceModel,
            )
            .join(
                ExternalSourceModel,
                (ExternalSourceModel.id == SourceAssetModel.external_source_id)
                & (ExternalSourceModel.tenant_id == SourceAssetModel.tenant_id),
            )
            .outerjoin(
                AssetSourceLinkModel,
                (AssetSourceLinkModel.source_asset_id == SourceAssetModel.id)
                & (AssetSourceLinkModel.tenant_id == SourceAssetModel.tenant_id),
            )
            .outerjoin(
                AssetModel,
                (AssetModel.id == AssetSourceLinkModel.asset_id)
                & (AssetModel.tenant_id == AssetSourceLinkModel.tenant_id),
            )
            .where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.deleted_at.is_(None),
            )
            .order_by(SourceAssetModel.id)
        ).all()
        result: list[CoverageResource] = []
        seen: set[str] = set()
        for source, link, asset, external in rows:
            if source.id in seen:
                continue
            seen.add(source.id)
            resolved_media_type = infer_media_type(
                source.filename,
                source.mime_type,
            )
            if not resolved_media_type.startswith("image/"):
                continue
            imported = bool(link and asset and asset.tenant_id == tenant_id)
            supported = is_eligible_image_source_asset(source)
            valid_hash = bool(
                asset
                and isinstance(asset.content_hash, str)
                and len(asset.content_hash) == 64
            )
            activity_values = [
                value
                for value in (
                    source.source_modified_at,
                    source.last_seen_at,
                    source.updated_at,
                    asset.updated_at if asset is not None else None,
                )
                if value is not None
            ]
            activity_at = max(activity_values) if activity_values else None
            result.append(
                CoverageResource(
                    source.id,
                    source.external_source_id,
                    external.source_type,
                    external.display_name,
                    source.mime_type,
                    asset.id if imported else None,
                    asset.content_hash if imported else None,
                    imported,
                    imported and supported and valid_hash,
                    not supported,
                    activity_at,
                )
            )
        return result

    def eligible_resources_page(
        self,
        tenant_id: str,
        *,
        after_asset_id: str | None = None,
        limit: int = 100,
    ) -> tuple[list[CoverageResource], bool]:
        """Return one bounded page of distinct eligible assets.

        Historical reconciliation loaded all SourceAssets on every slice. This
        cursor path keeps work proportional to the requested slice while
        preserving a deterministic asset-id checkpoint.
        """
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")

        image_predicates = [
            func.lower(func.coalesce(SourceAssetModel.mime_type, "")).like("image/%"),
            *[
                func.lower(func.coalesce(SourceAssetModel.filename, "")).like(
                    f"%{extension}"
                )
                for extension in IMAGE_MIME_BY_EXTENSION
            ],
        ]
        target = limit + 1
        result: list[CoverageResource] = []
        seen_assets: set[str] = set()
        cursor_asset = after_asset_id
        cursor_source: str | None = None

        while len(result) < target:
            remaining = target - len(result)
            fetch_size = min(500, max(50, remaining * 4))
            cursor_clause = None
            if cursor_asset is not None:
                if cursor_source is None:
                    cursor_clause = AssetModel.id > cursor_asset
                else:
                    cursor_clause = or_(
                        AssetModel.id > cursor_asset,
                        and_(
                            AssetModel.id == cursor_asset,
                            SourceAssetModel.id > cursor_source,
                        ),
                    )

            query = (
                select(
                    SourceAssetModel,
                    AssetSourceLinkModel,
                    AssetModel,
                    ExternalSourceModel,
                )
                .join(
                    AssetSourceLinkModel,
                    (AssetSourceLinkModel.source_asset_id == SourceAssetModel.id)
                    & (AssetSourceLinkModel.tenant_id == SourceAssetModel.tenant_id),
                )
                .join(
                    AssetModel,
                    (AssetModel.id == AssetSourceLinkModel.asset_id)
                    & (AssetModel.tenant_id == AssetSourceLinkModel.tenant_id),
                )
                .join(
                    ExternalSourceModel,
                    (ExternalSourceModel.id == SourceAssetModel.external_source_id)
                    & (ExternalSourceModel.tenant_id == SourceAssetModel.tenant_id),
                )
                .where(
                    SourceAssetModel.tenant_id == tenant_id,
                    SourceAssetModel.deleted_at.is_(None),
                    or_(*image_predicates),
                )
                .order_by(AssetModel.id, SourceAssetModel.id)
                .limit(fetch_size)
            )
            if cursor_clause is not None:
                query = query.where(cursor_clause)

            rows = self.session.execute(query).all()
            if not rows:
                break

            for source, link, asset, external in rows:
                cursor_asset = asset.id
                cursor_source = source.id
                if asset.id in seen_assets:
                    continue
                resource = self._resource(source, link, asset, external)
                if resource is None or not resource.eligible:
                    continue
                seen_assets.add(asset.id)
                result.append(resource)
                if len(result) >= target:
                    break

            if len(rows) < fetch_size:
                break

        return result[:limit], len(result) > limit
