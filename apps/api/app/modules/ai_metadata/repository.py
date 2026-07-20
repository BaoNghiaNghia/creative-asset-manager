from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any, Mapping

from jsonschema import SchemaError
from jsonschema.validators import validator_for
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel, utcnow
from app.modules.ai_metadata.validator import MetadataDocumentValidator
from app.modules.assets.model import AssetModel


class MetadataValidationFailure(ValueError):
    def __init__(self, errors):
        super().__init__("metadata document validation failed")
        self.errors = errors


class AiMetadataRepository:
    def __init__(self, session: Session, validator: MetadataDocumentValidator | None = None):
        self.session = session
        self.validator = validator or MetadataDocumentValidator()

    def create_profile(
        self, *, tenant_id: str, profile_name: str, profile_version: str,
        prompt_template: str, optional_json_schema: Mapping[str, Any] | None = None,
        search_config: Mapping[str, Any] | None = None, active: bool = True,
    ) -> MetadataProfileModel:
        if optional_json_schema is not None:
            try:
                cls = validator_for(optional_json_schema)
                cls.check_schema(optional_json_schema)
            except SchemaError as exc:
                raise ValueError(f"invalid metadata profile JSON Schema: {exc}") from exc
        profile = MetadataProfileModel(
            tenant_id=tenant_id, profile_name=profile_name,
            profile_version=profile_version, prompt_template=prompt_template,
            optional_json_schema=dict(optional_json_schema) if optional_json_schema is not None else None,
            search_config_json=dict(search_config or {}), active=active,
        )
        self.session.add(profile)
        self.session.flush()
        return profile

    def create_analysis(
        self, *, tenant_id: str, asset_id: str, metadata_profile_id: str,
        prompt_version: str, pipeline_version: str, ai_provider: str | None = None,
        ai_model: str | None = None, force: bool = False,
    ) -> AssetAiAnalysisModel:
        asset = self.session.get(AssetModel, asset_id)
        profile = self.session.get(MetadataProfileModel, metadata_profile_id)
        if asset is None or asset.tenant_id != tenant_id:
            raise LookupError(asset_id)
        if profile is None or profile.tenant_id != tenant_id:
            raise LookupError(metadata_profile_id)
        if not force:
            existing = self._normal_analysis(
                tenant_id, asset_id, asset.content_hash, metadata_profile_id,
                prompt_version, pipeline_version,
            )
            if existing is not None:
                return existing
        try:
            with self.session.begin_nested():
                analysis = AssetAiAnalysisModel(
                    tenant_id=tenant_id, asset_id=asset_id,
                    content_hash=asset.content_hash, metadata_profile_id=profile.id,
                    metadata_profile=profile.profile_name,
                    metadata_profile_version=profile.profile_version,
                    prompt_version=prompt_version, pipeline_version=pipeline_version,
                    ai_provider=ai_provider, ai_model=ai_model, forced=force,
                )
                self.session.add(analysis)
                self.session.flush()
            return analysis
        except IntegrityError:
            if force:
                raise
            existing = self._normal_analysis(
                tenant_id, asset_id, asset.content_hash, metadata_profile_id,
                prompt_version, pipeline_version,
            )
            if existing is None:
                raise
            return existing

    def mark_running(self, analysis_id: str) -> AssetAiAnalysisModel:
        analysis = self._analysis(analysis_id)
        if analysis.status == "completed":
            return analysis
        analysis.status = "running"
        analysis.attempt_count += 1
        analysis.started_at = utcnow()
        analysis.updated_at = utcnow()
        self.session.flush()
        return analysis

    def claim_analysis(
        self, analysis_id: str, *, worker_id: str, lease_seconds: int,
    ) -> AssetAiAnalysisModel | None:
        now = utcnow()
        statement = (
            update(AssetAiAnalysisModel)
            .where(
                AssetAiAnalysisModel.id == analysis_id,
                AssetAiAnalysisModel.status != "completed",
                or_(
                    AssetAiAnalysisModel.claimed_by.is_(None),
                    AssetAiAnalysisModel.claimed_by == worker_id,
                    AssetAiAnalysisModel.lease_expires_at < now,
                ),
            )
            .values(
                status="running", processing_stage="preparing",
                claimed_by=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempt_count=AssetAiAnalysisModel.attempt_count + 1,
                started_at=now, updated_at=now,
            )
            .returning(AssetAiAnalysisModel)
            .execution_options(synchronize_session=False)
        )
        return self.session.scalars(statement).first()

    def set_stage(self, analysis_id: str, stage: str) -> None:
        analysis = self._analysis(analysis_id)
        if analysis.status != "completed":
            analysis.processing_stage = stage[:40]
            analysis.updated_at = utcnow()
            self.session.flush()

    def save_analysis_image_hash(
        self, *, tenant_id: str, asset_id: str, image_hash: str,
    ) -> None:
        asset = self.session.get(AssetModel, asset_id)
        if asset is None or asset.tenant_id != tenant_id:
            raise LookupError(asset_id)
        asset.analysis_image_hash = image_hash
        asset.updated_at = utcnow()
        self.session.flush()

    def complete_analysis(
        self, *, analysis_id: str, metadata: str | bytes | Mapping[str, Any],
        raw_response: Mapping[str, Any] | None = None,
        store_raw_response: bool = False,
        search_projection: Mapping[str, Any] | None = None,
        search_projection_version: str | None = None,
        projection_checksum: str | None = None,
        provider_request_id: str | None = None,
        usage: Mapping[str, Any] | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
        ai_model: str | None = None,
    ) -> AssetAiAnalysisModel:
        analysis = self._analysis(analysis_id)
        if analysis.status == "completed":
            return analysis
        profile = self.session.get(MetadataProfileModel, analysis.metadata_profile_id)
        result = self.validator.validate(
            metadata, json_schema=profile.optional_json_schema if profile else None,
        )
        if not result.valid:
            raise MetadataValidationFailure(result.errors)
        now = utcnow()
        analysis.metadata_json = result.document
        analysis.raw_response_json = (
            dict(raw_response)
            if store_raw_response and raw_response is not None else None
        )
        analysis.search_projection = (
            dict(search_projection) if search_projection is not None else None
        )
        analysis.search_projection_version = search_projection_version
        analysis.projection_checksum = projection_checksum
        analysis.provider_request_id = provider_request_id
        analysis.usage_json = dict(usage or {})
        analysis.provider_metadata_json = dict(provider_metadata or {})
        analysis.ai_model = ai_model or analysis.ai_model
        analysis.status = "completed"
        analysis.processing_stage = "completed"
        analysis.claimed_by = None
        analysis.lease_expires_at = None
        analysis.failure_retryable = None
        analysis.last_error_code = None
        analysis.last_error_message = None
        analysis.completed_at = now
        analysis.updated_at = now
        self.session.flush()
        return analysis

    def mark_budget_blocked(self, analysis_id: str, *, code: str, reason: str) -> AssetAiAnalysisModel:
        analysis = self._analysis(analysis_id)
        if analysis.status == "completed":
            return analysis
        analysis.status = "budget_blocked"
        analysis.processing_stage = "budget_blocked"
        analysis.last_error_code = code[:100]
        analysis.last_error_message = reason
        analysis.failure_retryable = True
        analysis.claimed_by = None
        analysis.lease_expires_at = None
        analysis.updated_at = utcnow()
        self.session.flush()
        return analysis

    def fail_analysis(
        self, analysis_id: str, *, error_code: str, error_message: str,
        retryable: bool | None = None,
        validation_errors: list[dict[str, Any]] | None = None,
        terminal: bool = True,
    ) -> AssetAiAnalysisModel:
        analysis = self._analysis(analysis_id)
        if analysis.status == "completed":
            return analysis
        now = utcnow()
        analysis.status = "failed" if terminal else "pending"
        analysis.last_error_code = error_code[:100]
        analysis.last_error_message = error_message
        analysis.failure_retryable = retryable
        analysis.validation_errors_json = validation_errors
        analysis.processing_stage = "failed" if terminal else "retry"
        analysis.claimed_by = None
        analysis.lease_expires_at = None
        analysis.completed_at = now if terminal else None
        analysis.updated_at = now
        self.session.flush()
        return analysis

    def history(self, tenant_id: str, asset_id: str) -> list[AssetAiAnalysisModel]:
        return list(self.session.scalars(
            select(AssetAiAnalysisModel).where(
                AssetAiAnalysisModel.tenant_id == tenant_id,
                AssetAiAnalysisModel.asset_id == asset_id,
            ).order_by(AssetAiAnalysisModel.created_at)
        ))

    def get_analysis(self, analysis_id: str) -> AssetAiAnalysisModel:
        return self._analysis(analysis_id)

    def get_profile(self, profile_id: str) -> MetadataProfileModel:
        profile = self.session.get(MetadataProfileModel, profile_id)
        if profile is None:
            raise LookupError(profile_id)
        return profile

    def find_active_profile(
        self, *, tenant_id: str, profile_name: str,
        profile_version: str | None = None,
    ) -> MetadataProfileModel | None:
        statement = select(MetadataProfileModel).where(
            MetadataProfileModel.tenant_id == tenant_id,
            MetadataProfileModel.profile_name == profile_name,
            MetadataProfileModel.active.is_(True),
        )
        if profile_version is not None:
            statement = statement.where(
                MetadataProfileModel.profile_version == profile_version
            )
        return self.session.scalar(
            statement.order_by(MetadataProfileModel.created_at.desc()).limit(1)
        )

    def save_search_projection(
        self, analysis_id: str, *, projection: Mapping[str, Any],
        projection_version: str,
    ) -> AssetAiAnalysisModel:
        if not projection_version.strip():
            raise ValueError("projection_version is required")
        analysis = self._analysis(analysis_id)
        analysis.search_projection = copy.deepcopy(dict(projection))
        analysis.search_projection_version = projection_version
        analysis.updated_at = utcnow()
        self.session.flush()
        return analysis

    def _analysis(self, analysis_id: str) -> AssetAiAnalysisModel:
        analysis = self.session.get(AssetAiAnalysisModel, analysis_id)
        if analysis is None:
            raise LookupError(analysis_id)
        return analysis

    def _normal_analysis(
        self, tenant_id: str, asset_id: str, content_hash: str,
        profile_id: str, prompt_version: str, pipeline_version: str,
    ) -> AssetAiAnalysisModel | None:
        return self.session.scalar(select(AssetAiAnalysisModel).where(
            AssetAiAnalysisModel.tenant_id == tenant_id,
            AssetAiAnalysisModel.asset_id == asset_id,
            AssetAiAnalysisModel.content_hash == content_hash,
            AssetAiAnalysisModel.metadata_profile_id == profile_id,
            AssetAiAnalysisModel.prompt_version == prompt_version,
            AssetAiAnalysisModel.pipeline_version == pipeline_version,
            AssetAiAnalysisModel.forced.is_(False),
        ))
