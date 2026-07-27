from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.auth_persistence.repository import PersistentCloudSession
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.source_sync.model import SourceSyncRunModel


ACTIVE_JOB_STATUSES = ("pending", "processing", "retry")


@dataclass(frozen=True, slots=True)
class LoginSyncEnqueueResult:
    external_source_id: str
    job_id: str
    reconciliation: bool
    created: bool


class GoogleLoginSyncScheduler:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def enqueue(
        self,
        cloud_session: PersistentCloudSession,
    ) -> LoginSyncEnqueueResult | None:
        if not self.settings.GOOGLE_AUTO_SCAN_ON_LOGIN_ENABLED:
            return None
        tenant_id = cloud_session.active_tenant_id
        if not tenant_id:
            return None

        provider_account_id = str(cloud_session.user.get("id") or "")
        if not provider_account_id:
            raise ValueError("Google session has no provider account identity")

        source = AssetRegistryRepository(self.session).upsert_external_source(
            tenant_id=tenant_id,
            source_key=f"google-drive:{cloud_session.connection_id}",
            source_type="google_drive",
            display_name=cloud_session.user.get("email") or cloud_session.user.get("name"),
            source_metadata={
                "oauth_connection_id": cloud_session.connection_id,
                "provider_account_id": provider_account_id,
            },
        )
        self.session.flush()

        completed_full_sync = self.session.scalar(
            select(SourceSyncRunModel.id).where(
                SourceSyncRunModel.tenant_id == tenant_id,
                SourceSyncRunModel.external_source_id == source.id,
                SourceSyncRunModel.mode == "full",
                SourceSyncRunModel.status == "completed",
            ).limit(1)
        )
        reconciliation = bool(
            self.settings.GOOGLE_FULL_SCAN_ON_FIRST_LOGIN_ENABLED
            and completed_full_sync is None
        )
        active = self.session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "source_sync",
                ProcessingJobModel.entity_type == "external_source",
                ProcessingJobModel.entity_id == source.id,
                ProcessingJobModel.status.in_(ACTIVE_JOB_STATUSES),
            ).order_by(ProcessingJobModel.created_at.desc()).limit(1)
        )
        if active is not None:
            return LoginSyncEnqueueResult(
                source.id, active.id, reconciliation, False
            )

        sequence = int(self.session.scalar(
            select(func.count()).select_from(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type == "source_sync",
                ProcessingJobModel.entity_type == "external_source",
                ProcessingJobModel.entity_id == source.id,
            )
        ) or 0)
        job = ProcessingRepository(self.session).create_job(
            tenant_id=tenant_id,
            job_type="source_sync",
            entity_type="external_source",
            entity_id=source.id,
            idempotency_key=f"google-login-source-sync:{source.id}:{sequence}",
            payload={
                "external_source_id": source.id,
                "oauth_connection_id": cloud_session.connection_id,
                "reconciliation": reconciliation,
            },
            priority=10,

            provider_key="google_drive",
            provider_scope="source",
        )
        return LoginSyncEnqueueResult(source.id, job.id, reconciliation, True)


def enqueue_google_login_sync(
    cloud_session: PersistentCloudSession,
    settings: Settings | None = None,
) -> LoginSyncEnqueueResult | None:
    with SessionLocal() as session:
        result = GoogleLoginSyncScheduler(
            session, settings or get_settings()
        ).enqueue(cloud_session)
        session.commit()
        return result
