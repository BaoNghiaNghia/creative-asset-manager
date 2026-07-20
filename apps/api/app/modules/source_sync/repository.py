from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.modules.assets.model import ExternalSourceModel, SourceAssetModel, SourceSyncCursorModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.source_sync.model import SourceSyncRunModel


class SourceSyncRepository:
    def __init__(self, session: Session):
        self.session = session
        self.assets = AssetRegistryRepository(session)

    def get_source(self, tenant_id: str, source_id: str) -> ExternalSourceModel | None:
        return self.session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.id == source_id,
        ))

    def get_source_asset_by_external_id(self, tenant_id: str, source_id: str, external_asset_id: str) -> SourceAssetModel | None:
        return self.session.scalar(select(SourceAssetModel).where(
            SourceAssetModel.tenant_id == tenant_id,
            SourceAssetModel.external_source_id == source_id,
            SourceAssetModel.external_asset_id == external_asset_id,
        ))

    def get_cursor(self, tenant_id: str, source_id: str, cursor_key: str = "changes") -> str | None:
        cursor = self.session.scalar(select(SourceSyncCursorModel).where(
            SourceSyncCursorModel.tenant_id == tenant_id,
            SourceSyncCursorModel.external_source_id == source_id,
            SourceSyncCursorModel.cursor_key == cursor_key,
        ))
        return cursor.cursor_value if cursor else None

    def start_or_resume_full_run(self, tenant_id: str, source_id: str) -> SourceSyncRunModel:
        # Locking the source serializes generation allocation on PostgreSQL.
        source = self.session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.id == source_id,
        ).with_for_update())
        if source is None:
            raise LookupError(source_id)
        run = self.session.scalar(select(SourceSyncRunModel).where(
            SourceSyncRunModel.tenant_id == tenant_id,
            SourceSyncRunModel.external_source_id == source_id,
            SourceSyncRunModel.mode == "full",
            SourceSyncRunModel.status.in_(("running", "failed")),
        ).order_by(SourceSyncRunModel.generation.desc()).limit(1))
        now = datetime.now(timezone.utc)
        if run is not None:
            run.status = "running"
            run.failed_at = None
            run.error_json = None
            run.updated_at = now
            self.session.flush()
            return run
        generation = int(self.session.scalar(select(func.coalesce(func.max(SourceSyncRunModel.generation), 0)).where(
            SourceSyncRunModel.tenant_id == tenant_id,
            SourceSyncRunModel.external_source_id == source_id,
        )) or 0) + 1
        run = SourceSyncRunModel(
            tenant_id=tenant_id,
            external_source_id=source_id,
            mode="full",
            generation=generation,
            status="running",
        )
        self.session.add(run)
        self.session.flush()
        return run

    def active_full_generation(self, tenant_id: str, source_id: str) -> int | None:
        return self.session.scalar(select(SourceSyncRunModel.generation).where(
            SourceSyncRunModel.tenant_id == tenant_id,
            SourceSyncRunModel.external_source_id == source_id,
            SourceSyncRunModel.mode == "full",
            SourceSyncRunModel.status == "running",
        ).order_by(SourceSyncRunModel.generation.desc()).limit(1))

    def mark_seen(self, source_asset: SourceAssetModel, generation: int, now: datetime | None = None) -> None:
        source_asset.last_seen_generation = generation
        source_asset.last_seen_at = now or datetime.now(timezone.utc)
        self.session.flush()

    def checkpoint_run(self, run: SourceSyncRunModel, *, cursor: str | None, items: int, jobs: int) -> None:
        run.checkpoint_cursor = cursor
        run.pages_count += 1
        run.items_seen_count += items
        run.jobs_created_count += jobs
        run.updated_at = datetime.now(timezone.utc)
        self.session.flush()

    def fail_run(self, run_id: str, error_code: str) -> None:
        run = self.session.get(SourceSyncRunModel, run_id)
        if run is None:
            return
        now = datetime.now(timezone.utc)
        run.status = "failed"
        run.failed_at = now
        run.updated_at = now
        run.error_json = {"code": error_code, "message": "Source enumeration did not complete."}
        self.session.flush()

    def cancel_run(self, run_id: str, reason: str = "cancelled") -> None:
        run = self.session.get(SourceSyncRunModel, run_id)
        if run is None or run.status == "completed":
            return
        now = datetime.now(timezone.utc)
        run.status = "cancelled"
        run.updated_at = now
        run.error_json = {"code": reason, "message": "Source enumeration was interrupted."}
        self.session.flush()

    def complete_full_run(self, run: SourceSyncRunModel) -> int:
        owned = self.session.scalar(
            select(SourceSyncRunModel)
            .where(SourceSyncRunModel.id == run.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if owned is None or owned.status != "running":
            raise RuntimeError("only the owning running reconciliation can complete")
        now = datetime.now(timezone.utc)
        result = self.session.execute(update(SourceAssetModel).where(
            SourceAssetModel.tenant_id == owned.tenant_id,
            SourceAssetModel.external_source_id == owned.external_source_id,
            SourceAssetModel.deleted_at.is_(None),
            or_(
                SourceAssetModel.last_seen_generation.is_(None),
                SourceAssetModel.last_seen_generation != owned.generation,
            ),
        ).values(deleted_at=now, updated_at=now).execution_options(synchronize_session=False))
        owned.status = "completed"
        owned.completed_at = now
        owned.updated_at = now
        owned.error_json = None
        self.session.flush()
        return int(result.rowcount or 0)
