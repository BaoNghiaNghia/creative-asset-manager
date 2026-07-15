import asyncio
import os

import httpx

from app.providers.microsoft.mapper import (
    ROOT_ID,
    make_id,
    map_drive,
    map_item,
    map_site,
    parse_id,
    root_node,
)


class SharePointClient:
    def __init__(self, access_token: str):
        self.client = httpx.AsyncClient(
            base_url="https://graph.microsoft.com/v1.0",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(25, connect=8),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.client.aclose()

    async def _get(self, path: str, params: dict | None = None):
        response = None
        for attempt in range(3):
            response = await self.client.get(path, params=params)
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt < 2:
                retry_after = response.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2 ** attempt)
                await asyncio.sleep(delay)
        response.raise_for_status()
        return response.json()

    async def _get_all(self, path: str, params: dict | None = None) -> list[dict]:
        values: list[dict] = []
        next_url: str | None = path
        next_params = params
        while next_url:
            data = await self._get(next_url, next_params)
            values.extend(data.get("value") or [])
            next_url = data.get("@odata.nextLink")
            next_params = None
        return values

    async def sites(self) -> list:
        hostname = os.getenv("SHAREPOINT_SITE_HOSTNAME")
        site_path = os.getenv("SHAREPOINT_SITE_PATH")
        if hostname and site_path:
            site = await self._get(f"/sites/{hostname}:/{site_path.lstrip('/')}")
            return [map_site(site)]

        try:
            sites = await self._get_all(
                "/sites",
                {"search": "*", "$select": "id,name,displayName,webUrl"},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {400, 403}:
                raise
            sites = await self._get_all(
                "/me/followedSites",
                {"$select": "id,name,displayName,webUrl"},
            )
        unique = {site["id"]: site for site in sites}
        return [map_site(site) for site in sorted(unique.values(), key=lambda value: (value.get("displayName") or "").lower())]

    async def get(self, item_id: str):
        kind, parts = parse_id(item_id)
        if kind == "root":
            return root_node()
        if kind == "site":
            site = await self._get(
                f"/sites/{parts[0]}",
                {"$select": "id,name,displayName,webUrl"},
            )
            return map_site(site)
        if kind == "drive":
            site_id, drive_id = parts
            drive = await self._get(
                f"/drives/{drive_id}",
                {"$select": "id,name,webUrl,driveType"},
            )
            return map_drive(drive, site_id, make_id("site", site_id))
        if kind == "item":
            drive_id, graph_item_id = parts
            item = await self._get(
                f"/drives/{drive_id}/items/{graph_item_id}",
                {"$expand": "thumbnails", "$select": "id,name,size,lastModifiedDateTime,webUrl,parentReference,file,folder,thumbnails"},
            )
            return map_item(item, drive_id)
        raise ValueError("Unsupported SharePoint node type")

    async def children(self, parent_id: str, folders_only: bool = False):
        kind, parts = parse_id(parent_id)
        if kind == "root":
            nodes = await self.sites()
        elif kind == "site":
            site_id = parts[0]
            drives = await self._get_all(
                f"/sites/{site_id}/drives",
                {"$select": "id,name,webUrl,driveType"},
            )
            nodes = [map_drive(drive, site_id, parent_id) for drive in drives]
        elif kind == "drive":
            _, drive_id = parts
            items = await self._get_all(
                f"/drives/{drive_id}/root/children",
                {
                    "$expand": "thumbnails",
                    "$select": "id,name,size,lastModifiedDateTime,webUrl,parentReference,file,folder,thumbnails",
                    "$top": "200",
                },
            )
            nodes = [map_item(item, drive_id, parent_id) for item in items]
        elif kind == "item":
            drive_id, graph_item_id = parts
            items = await self._get_all(
                f"/drives/{drive_id}/items/{graph_item_id}/children",
                {
                    "$expand": "thumbnails",
                    "$select": "id,name,size,lastModifiedDateTime,webUrl,parentReference,file,folder,thumbnails",
                    "$top": "200",
                },
            )
            nodes = [map_item(item, drive_id, parent_id) for item in items]
        else:
            nodes = []

        if folders_only:
            return [node for node in nodes if node.kind == "folder"]
        return nodes


async def open_media_stream(access_token: str, item_id: str, range_header: str | None):
    kind, parts = parse_id(item_id)
    if kind != "item":
        raise ValueError("SharePoint media preview requires a file item")
    drive_id, graph_item_id = parts

    client = httpx.AsyncClient(timeout=httpx.Timeout(25, read=None), follow_redirects=True)
    headers = {"Authorization": f"Bearer {access_token}"}
    if range_header:
        headers["Range"] = range_header
    request = client.build_request(
        "GET",
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{graph_item_id}/content",
        headers=headers,
    )
    response = await client.send(request, stream=True)
    try:
        response.raise_for_status()
    except Exception:
        await response.aclose()
        await client.aclose()
        raise
    return client, response


async def close_media_stream(client: httpx.AsyncClient, response: httpx.Response):
    await response.aclose()
    await client.aclose()
