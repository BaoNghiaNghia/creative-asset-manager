from __future__ import annotations

from app.modules.ai_metadata.projection import (
    SearchProjectionBuilder,
    SearchProjectionBuildResult,
)
from app.modules.ai_metadata.repository import AiMetadataRepository


class SearchProjectionService:
    """Rebuilds the PostgreSQL projection from stored metadata without AI."""

    def __init__(
        self,
        repository: AiMetadataRepository,
        builder: SearchProjectionBuilder,
        *,
        enabled: bool = False,
    ):
        self.repository = repository
        self.builder = builder
        self.enabled = enabled

    def rebuild(self, analysis_id: str) -> SearchProjectionBuildResult:
        if not self.enabled:
            raise RuntimeError("search projection is disabled")

        analysis = self.repository.get_analysis(analysis_id)
        if analysis.metadata_json is None:
            raise ValueError("analysis has no validated metadata document")
        profile = self.repository.get_profile(analysis.metadata_profile_id)
        result = self.builder.build(
            analysis.metadata_json,
            profile.search_config_json,
        )
        self.repository.save_search_projection(
            analysis.id,
            projection=result.projection.to_document(),
            projection_version=result.projection_version,
        )
        return result
