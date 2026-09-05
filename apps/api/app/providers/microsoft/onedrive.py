from __future__ import annotations
import asyncio
from urllib.parse import urlparse
import httpx
from app.providers.microsoft.onedrive_mapper import ONEDRIVE_ROOT_ID,map_item,parse_item_id,root_node
TRANSIENT={429,500,502,503,504}
class OneDriveThumbnailUnavailable(Exception):
    pass

def validate_graph_url(value:str)->str:
    parsed=urlparse(value)
    if parsed.scheme!="https" or parsed.hostname!="graph.microsoft.com" or not parsed.path.startswith("/v1.0/"):raise ValueError("Microsoft Graph continuation URL is invalid")
    return value
class OneDriveClient:
    def __init__(self,access_token:str,*,client:httpx.AsyncClient|None=None,sleeper=asyncio.sleep):
        self.client=client or httpx.AsyncClient(base_url="https://graph.microsoft.com/v1.0",headers={"Authorization":f"Bearer {access_token}"},timeout=httpx.Timeout(25,connect=8));self._owned=client is None;self._sleeper=sleeper
    async def __aenter__(self):return self
    async def __aexit__(self,*args):
        if self._owned:await self.client.aclose()
    async def _get(self,url:str,params:dict|None=None)->dict:
        for attempt in range(3):
            response=await self.client.get(url,params=params)
            if response.status_code not in TRANSIENT or attempt==2:break
            try:delay=min(float(response.headers.get("retry-after","")),5.0)
            except ValueError:delay=.25*(2**attempt)
            await self._sleeper(delay)
        response.raise_for_status();return response.json()
    async def drive(self)->dict:return await self._get("/me/drive",{"$select":"id,driveType,name,webUrl,owner"})
    async def get(self,item_id:str):
        if item_id==ONEDRIVE_ROOT_ID:return root_node()
        drive_id,graph_id=parse_item_id(item_id);item=await self._get(f"/drives/{drive_id}/items/{graph_id}",{"$select":"id,name,size,lastModifiedDateTime,webUrl,parentReference,file,folder"});return map_item(item,drive_id)
    async def children_page(self,parent_id:str,*,folders_only=False,page_token=None,page_size=100):
        if page_token:
            url=validate_graph_url(page_token);params=None
            path=urlparse(url).path.split("/")
            drive_id=path[3] if len(path)>3 and path[2]=="drives" else ""
        elif parent_id==ONEDRIVE_ROOT_ID:
            drive=await self.drive();drive_id=str(drive["id"]);url=f"/drives/{drive_id}/root/children";params={"$select":"id,name,size,lastModifiedDateTime,webUrl,parentReference,file,folder","$top":str(min(max(page_size,1),200))}
        else:
            drive_id,graph_id=parse_item_id(parent_id);url=f"/drives/{drive_id}/items/{graph_id}/children";params={"$select":"id,name,size,lastModifiedDateTime,webUrl,parentReference,file,folder","$top":str(min(max(page_size,1),200))}
        data=await self._get(url,params);nodes=[map_item(item,str((item.get("parentReference") or {}).get("driveId") or drive_id),parent_id) for item in data.get("value") or []]
        if folders_only:nodes=[node for node in nodes if node.kind=="folder"]
        next_link=data.get("@odata.nextLink");return nodes,validate_graph_url(next_link) if next_link else None
    async def children(self,parent_id:str,folders_only=False):
        nodes=[];token=None
        while True:
            page,token=await self.children_page(parent_id,folders_only=folders_only,page_token=token,page_size=200);nodes.extend(page)
            if not token:return nodes
async def open_media_stream(access_token:str,item_id:str,range_header:str|None):
    drive_id,graph_id=parse_item_id(item_id);client=httpx.AsyncClient(timeout=httpx.Timeout(25,read=None),follow_redirects=True);headers={"Authorization":f"Bearer {access_token}"}
    if range_header:headers["Range"]=range_header
    response=await client.send(client.build_request("GET",f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{graph_id}/content",headers=headers),stream=True)
    try:response.raise_for_status()
    except Exception:await response.aclose();await client.aclose();raise
    return client,response
async def close_media_stream(client:httpx.AsyncClient,response:httpx.Response):await response.aclose();await client.aclose()

async def open_thumbnail_stream(access_token:str,item_id:str):
    drive_id,graph_id=parse_item_id(item_id)
    client=httpx.AsyncClient(timeout=httpx.Timeout(25,read=None),follow_redirects=True)
    response=await client.send(client.build_request(
        "GET",
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{graph_id}/thumbnails/0/large/content",
        headers={"Authorization":f"Bearer {access_token}"},
    ),stream=True)
    if response.status_code in {400,404}:
        await response.aclose();await client.aclose()
        raise OneDriveThumbnailUnavailable(item_id)
    try:response.raise_for_status()
    except Exception:await response.aclose();await client.aclose();raise
    return client,response

async def close_thumbnail_stream(client:httpx.AsyncClient,response:httpx.Response):await response.aclose();await client.aclose()
