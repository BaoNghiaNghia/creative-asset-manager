from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.content_identity import ensure_source_asset_link, normalize_sha256
from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.mime_types import is_eligible_video_source_asset


@dataclass(frozen=True, slots=True)
class VideoAssetLinkRepairResult:
    scanned: int
    eligible_videos: int
    repairable_sha256: int
    linked: int
    skipped_without_sha256: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def repair_video_asset_links(
    session: Session,
    *,
    tenant_id: str | None = None,
    limit: int = 1000,
    execute: bool = False,
) -> VideoAssetLinkRepairResult:
    if not 1 <= limit <= 100_000:
        raise ValueError("limit must be between 1 and 100000")

    statement = (
        select(SourceAssetModel)
        .outerjoin(
            AssetSourceLinkModel,
            (AssetSourceLinkModel.tenant_id == SourceAssetModel.tenant_id)
            & (AssetSourceLinkModel.source_asset_id == SourceAssetModel.id),
        )
        .where(
            SourceAssetModel.deleted_at.is_(None),
            SourceAssetModel.is_folder.is_(False),
            SourceAssetModel.mime_type.like("video/%"),
            AssetSourceLinkModel.id.is_(None),
        )
        .order_by(SourceAssetModel.id)
        .limit(limit)
    )
    if tenant_id is not None:
        statement = statement.where(SourceAssetModel.tenant_id == tenant_id)

    rows = list(session.scalars(statement))
    repository = AssetRegistryRepository(session)
    eligible = repairable = linked = skipped = 0
    for source in rows:
        if not is_eligible_video_source_asset(source):
            continue
        eligible += 1
        checksum = normalize_sha256(source.provider_checksum)
        if checksum is None:
            skipped += 1
            continue
        repairable += 1
        if execute:
            ensure_source_asset_link(
                repository,
                source_asset=source,
                content_hash=checksum,
            )
            linked += 1

    if execute:
        session.commit()
    return VideoAssetLinkRepairResult(
        scanned=len(rows),
        eligible_videos=eligible,
        repairable_sha256=repairable,
        linked=linked,
        skipped_without_sha256=skipped,
    )