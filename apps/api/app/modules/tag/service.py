import os
import httpx
from app.modules.tag.schema import Tag

DEMO_TAGS = [Tag(id="approved", name="Approved", color="#26a269"), Tag(id="review", name="Needs review", color="#e5a50a"), Tag(id="social", name="Social", color="#3584e4")]

class TagService:
    def __init__(self):
        self.url, self.token = os.getenv("DIRECTUS_URL"), os.getenv("DIRECTUS_TOKEN")

    async def list(self):
        if not self.url or not self.token: return DEMO_TAGS
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}) as client:
            response = await client.get(f"{self.url.rstrip('/')}/items/asset_tags", params={"fields": "id,name,color", "sort": "name"})
            response.raise_for_status()
            return response.json()["data"]

    async def assign(self, item_ids: list[str], tag_id: str):
        rows = [{"provider": "google-drive", "item_id": item_id, "tag_id": tag_id} for item_id in item_ids]
        if not self.url or not self.token: return rows
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}) as client:
            response = await client.post(f"{self.url.rstrip('/')}/items/asset_tag_assignments", json=rows)
            response.raise_for_status()
            return response.json()["data"]
