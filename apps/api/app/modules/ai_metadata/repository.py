from __future__ import annotations

import copy
from typing import Any, Mapping

from jsonschema import SchemaError
from jsonschema.validators import validator_for
from sqlalchemy import select
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
        self,
        *,
        tenant_id: str,
        profile_name: str,
        profile_version: str,
        prompt_template: str,
        optional_json_schema: Mapping[str, Any] | None = None,
        search_config: Mapping[str, Any] | None = None,
        active: bool = True,
    ) -> MetadataProfileModel:
        if optional_json_schema is not None:
            try:
                cls = validator_for(optional_json_schema)
                cls.check_schema(optional_json_schema)
            except SchemaError as exc:
                raise ValueError(f"invalid metadata profile JSON Schema: {exc}") from exc
        profile = MetadataProfileModel(
            tenant_id=tenant_id,
            profile_name=profile_name,
            profile_version=profile_version,
            prompt_template=prompt_template,
            optional_json_schema=dict(optional_json_schema) if optional_json_schema is not None else None,
            search_config_json=dict(search_config or {}),
            active=active,
        )
        self.session.add(profile)
        self.session.flush()
        return profile

    def create_analysis(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        metadata_profile_id: str,
        prompt_version: str,
        pipeline_version: str,
        ai_provider: str | None = None,
        ai_model: str | None = None,
        force: bool = False,
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
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    content_hash=asset.content_hash,
                    metadata_profile_id=profile.id,
                    metadata_profile=profile.profile_name,
                    metadata_profile_version=profile.profile_version,
                    prompt_version=prompt_version,
                    pipeline_version=pipeline_version,
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    forced=force,
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

    def complete_analysis(
        self,
        *,
        analysis_id: str,
        metadata: str | bytes | Mapping[str, Any],
        raw_response: Mapping[str, Any] | None = None,
        store_raw_response: bool = False,
        search_projection: Mapping[str, Any] | None = None,
        search_projection_version: str | None = None,
    ) -> AssetAiAnalysisModel:
        analysis = self._analysis(analysis_id)
        if analysis.status == "completed":
            return analysis
        profile = self.session.get(MetadataProfileModel, analysis.metadata_profile_id)
        result = self.validator.validate(
            metadata,
            json_schema=profile.optional_json_schema if profile else None,
        )
        if not result.valid:
            raise MetadataValidationFailure(result.errors)
        now = utcnow()
        analysis.metadata_json = result.document
        analysis.raw_response_json = dict(raw_response) if store_raw_response and raw_response is not None else None
        analysis.search_projection = dict(search_projection) if search_projection is not None else None
        analysis.search_projection_version = search_projection_version
        analysis.status = "completed"
        analysis.last_error_code = None
        analysis.last_error_message = None
        analysis.completed_at = now
        analysis.updated_at = now
        self.session.flush()
        return analysis

    def fail_analysis(
        self, analysis_id: str, *, error_code: str, error_message: str
    ) -> AssetAiAnalysisModel:
        analysis = self._analysis(analysis_id)
        if analysis.status == "completed":
            return analysis
        analysis.status = "failed"
        analysis.last_error_code = error_code[:100]
        analysis.last_error_message = error_message
        analysis.completed_at = utcnow()
        analysis.updated_at = utcnow()
        self.session.flush()
        return analysis

    def history(self, tenant_id: str, asset_id: str) -> list[AssetAiAnalysisModel]:
        return list(
            self.session.scalars(
                select(AssetAiAnalysisModel)
                .where(
                    AssetAiAnalysisModel.tenant_id == tenant_id,
                    AssetAiAnalysisModel.asset_id == asset_id,
                )
                .order_by(AssetAiAnalysisModel.created_at)
            )
        )

    def get_analysis(self, analysis_id: str) -> AssetAiAnalysisModel:
        return self._analysis(analysis_id)

    def get_profile(self, profile_id: str) -> MetadataProfileModel:
        profile = self.session.get(MetadataProfileModel, profile_id)
        if profile is None:
            raise LookupError(profile_id)
        return profile

    def save_search_projection(
        self,
        analysis_id: str,
        *,
        projection: Mapping[str, Any],
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
        self,
        tenant_id: str,
        asset_id: str,
        content_hash: str,
        profile_id: str,
        prompt_version: str,
        pipeline_version: str,
    ) -> AssetAiAnalysisModel | None:
        return self.session.scalar(
            select(AssetAiAnalysisModel).where(
                AssetAiAnalysisModel.tenant_id == tenant_id,
                AssetAiAnalysisModel.asset_id == asset_id,
                AssetAiAnalysisModel.content_hash == content_hash,
                AssetAiAnalysisModel.metadata_profile_id == profile_id,
                AssetAiAnalysisModel.prompt_version == prompt_version,
                AssetAiAnalysisModel.pipeline_version == pipeline_version,
                AssetAiAnalysisModel.forced.is_(False),
            )
        )
