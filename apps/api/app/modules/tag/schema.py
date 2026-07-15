from pydantic import BaseModel, Field

class Tag(BaseModel):
    id: str
    name: str
    color: str = "#3584e4"

class AssignTagsRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    tag_id: str
