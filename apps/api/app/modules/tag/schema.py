from pydantic import BaseModel, Field

from app.modules.explorer.schema import Provider

class Tag(BaseModel):
    id: str
    name: str
    color: str = "#3584e4"

class AssignTagsRequest(BaseModel):
    provider: Provider = "google-drive"
    item_ids: list[str] = Field(min_length=1)
    tag_id: str
