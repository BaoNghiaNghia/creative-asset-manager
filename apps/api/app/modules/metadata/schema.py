from pydantic import BaseModel, Field

from app.modules.explorer.schema import Provider


class MetadataQueryRequest(BaseModel):
    provider: Provider = "google-drive"
    item_ids: list[str] = Field(min_length=1, max_length=500)


class SetRatingRequest(MetadataQueryRequest):
    rating: int | None = Field(default=None, ge=1, le=5)
