from typing import Literal

from pydantic import BaseModel, Field


class EnqueueAssetAnalysisRequest(BaseModel):
    asset_id: str = Field(min_length=1, max_length=36)
    metadata_profile: str = Field(min_length=1, max_length=255)
    metadata_profile_version: str | None = Field(default=None, max_length=100)
    source_provider: Literal["google-drive", "sharepoint"] = "google-drive"
    force: bool = False


class EnqueueAssetAnalysisResponse(BaseModel):
    analysis_id: str
    job_id: str
    status: Literal["accepted"]
