from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.modules.assets.model import ExternalSourceModel
from app.modules.external_ingestion.model import (
    AssetIngestionItemModel,
    AssetIngestionModel,
    ExternalApiCredentialModel,
    ExternalApiRateLimitModel,
)


class IdempotencyConflictError(RuntimeError):
    pass


class RateLimitExceededError(RuntimeError):
    def __init__(self, retry_after_seconds: int):
        super().__init__("external API rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class ExternalIngestionRepository:
    """Persistence boundary; methods flush but the service owns commits."""

    def __init__(self, session: Session):
        self.session = session

    def create_credential(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        name: str,
        raw_key: str,
        rate_limit_per_minute: int = 60,
    ) -> ExternalApiCredentialModel:
        if len(raw_key) < 32 or len(raw_key) > 512 or raw_key.strip() != raw_key:
            raise ValueError("API keys must contain 32 to 512 non-whitespace-boundary characters")
        if rate_limit_per_minute < 1 or rate_limit_per_minute > 100_000:
            raise ValueError("rate_limit_per_minute is out of range")
        source = self.get_source(tenant_id, external_source_id)
        if source is None or source.source_type != "external_api":
            raise ValueError("credential source must be an external_api source")
        credential = ExternalApiCredentialModel(
            tenant_id=tenant_id,
            external_source_id=external_source_id,
            name=name,
            key_prefix=raw_key[:8],
            secret_hash=hash_api_key(raw_key),
            rate_limit_per_minute=rate_limit_per_minute,
        )
        self.session.add(credential)
        self.session.flush()
        return credential

    def authenticate(self, raw_key: str) -> ExternalApiCredentialModel | None:
        if len(raw_key) < 32 or len(raw_key) > 512 or raw_key.strip() != raw_key:
            return None
        return self.session.scalar(
            select(ExternalApiCredentialModel).where(
                ExternalApiCredentialModel.secret_hash == hash_api_key(raw_key),
                ExternalApiCredentialModel.active.is_(True),
                ExternalApiCredentialModel.revoked_at.is_(None),
            )
        )

    def get_source(self, tenant_id: str, source_id: str) -> ExternalSourceModel | None:
        return self.session.scalar(
            select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.id == source_id,
            )
        )

    def consume_rate_limit(
        self,
        credential: ExternalApiCredentialModel,
        *,
        now: datetime | None = None,
    ) -> tuple[int, int]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        window = current.replace(second=0, microsecond=0)
        limit = credential.rate_limit_per_minute
        dialect = self.session.get_bind().dialect.name
        insert_factory = (
            postgresql_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
        )
        if insert_factory is not None:
            statement = insert_factory(ExternalApiRateLimitModel).values(
                id=str(uuid4()),
                credential_id=credential.id,
                window_start=window,
                request_count=1,
            )
            statement = statement.on_conflict_do_update(
                index_elements=["credential_id", "window_start"],
                set_={"request_count": ExternalApiRateLimitModel.request_count + 1},
                where=ExternalApiRateLimitModel.request_count < limit,
            ).returning(ExternalApiRateLimitModel.request_count)
            count = self.session.scalar(statement)
        else:
            counter = self.session.scalar(
                select(ExternalApiRateLimitModel)
                .where(
                    ExternalApiRateLimitModel.credential_id == credential.id,
                    ExternalApiRateLimitModel.window_start == window,
                )
                .with_for_update()
            )
            if counter is None:
                counter = ExternalApiRateLimitModel(
                    credential_id=credential.id,
                    window_start=window,
                    request_count=1,
                )
                self.session.add(counter)
                self.session.flush()
                count = 1
            elif counter.request_count < limit:
                counter.request_count += 1
                self.session.flush()
                count = counter.request_count
            else:
                count = None
        if count is None:
            retry_after = max(1, 60 - current.second)
            raise RateLimitExceededError(retry_after)
        return int(count), max(0, limit - int(count))

    def get_by_idempotency_key(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        idempotency_key: str,
    ) -> AssetIngestionModel | None:
        return self.session.scalar(
            select(AssetIngestionModel).where(
                AssetIngestionModel.tenant_id == tenant_id,
                AssetIngestionModel.external_source_id == external_source_id,
                AssetIngestionModel.idempotency_key == idempotency_key,
            )
        )

    def create_ingestion(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        credential_id: str,
        idempotency_key: str,
        request_hash: str,
        request_json: Mapping[str, Any],
        items: Sequence[Mapping[str, Any]],
    ) -> tuple[AssetIngestionModel, list[AssetIngestionItemModel]]:
        ingestion = AssetIngestionModel(
            tenant_id=tenant_id,
            external_source_id=external_source_id,
            credential_id=credential_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_json=dict(request_json),
            received_count=len(items),
        )
        self.session.add(ingestion)
        self.session.flush()
        records = [
            AssetIngestionItemModel(
                tenant_id=tenant_id,
                ingestion_id=ingestion.id,
                position=position,
                external_asset_id=str(item["external_asset_id"]),
                download_url=str(item["download_url"]),
                provider_checksum=item.get("checksum"),
                filename=item.get("filename"),
                source_modified_at=item.get("modified_at"),
            )
            for position, item in enumerate(items)
        ]
        self.session.add_all(records)
        self.session.flush()
        return ingestion, records

    def get_ingestion(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        ingestion_id: str,
    ) -> AssetIngestionModel | None:
        return self.session.scalar(
            select(AssetIngestionModel).where(
                AssetIngestionModel.tenant_id == tenant_id,
                AssetIngestionModel.external_source_id == external_source_id,
                AssetIngestionModel.id == ingestion_id,
            )
        )

    def list_items(
        self,
        *,
        tenant_id: str,
        ingestion_id: str,
        limit: int,
        offset: int,
    ) -> list[AssetIngestionItemModel]:
        return list(
            self.session.scalars(
                select(AssetIngestionItemModel)
                .where(
                    AssetIngestionItemModel.tenant_id == tenant_id,
                    AssetIngestionItemModel.ingestion_id == ingestion_id,
                )
                .order_by(AssetIngestionItemModel.position)
                .offset(offset)
                .limit(limit)
            )
        )

    def status_counts(self, tenant_id: str, ingestion_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(AssetIngestionItemModel.status, func.count())
            .where(
                AssetIngestionItemModel.tenant_id == tenant_id,
                AssetIngestionItemModel.ingestion_id == ingestion_id,
            )
            .group_by(AssetIngestionItemModel.status)
        )
        return {status: int(count) for status, count in rows}

    def update_item_status(
        self,
        *,
        tenant_id: str,
        ingestion_id: str,
        item_id: str,
        status: str,
        source_asset_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> AssetIngestionItemModel:
        if status not in {"queued", "processing", "completed", "failed"}:
            raise ValueError("unsupported ingestion item status")
        item = self.session.scalar(
            select(AssetIngestionItemModel).where(
                AssetIngestionItemModel.tenant_id == tenant_id,
                AssetIngestionItemModel.ingestion_id == ingestion_id,
                AssetIngestionItemModel.id == item_id,
            )
        )
        if item is None:
            raise LookupError(item_id)
        changed_at = now or datetime.now(timezone.utc)
        item.status = status
        item.source_asset_id = source_asset_id
        item.last_error_code = error_code
        item.last_error_message = error_message
        item.updated_at = changed_at
        item.completed_at = changed_at if status in {"completed", "failed"} else None
        self.session.flush()

        ingestion = self.session.scalar(
            select(AssetIngestionModel).where(
                AssetIngestionModel.tenant_id == tenant_id,
                AssetIngestionModel.id == ingestion_id,
            )
        )
        if ingestion is None:
            raise LookupError(ingestion_id)
        counts = self.status_counts(tenant_id, ingestion_id)
        if counts.get("queued", 0) == ingestion.received_count:
            ingestion.status = "accepted"
        elif counts.get("queued", 0) or counts.get("processing", 0):
            ingestion.status = "processing"
        elif counts.get("completed", 0) == ingestion.received_count:
            ingestion.status = "completed"
        elif counts.get("failed", 0) == ingestion.received_count:
            ingestion.status = "failed"
        else:
            ingestion.status = "partial_failed"
        ingestion.updated_at = changed_at
        ingestion.completed_at = (
            changed_at
            if ingestion.status in {"completed", "partial_failed", "failed"}
            else None
        )
        self.session.flush()
        return item

    def recover_idempotency_conflict(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> AssetIngestionModel:
        ingestion = self.get_by_idempotency_key(
            tenant_id=tenant_id,
            external_source_id=external_source_id,
            idempotency_key=idempotency_key,
        )
        if ingestion is None:
            raise LookupError(idempotency_key)
        if ingestion.request_hash != request_hash:
            raise IdempotencyConflictError(idempotency_key)
        return ingestion
