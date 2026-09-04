from __future__ import annotations
import base64
from app.modules.explorer.schema import AssetNode
ONEDRIVE_ROOT_ID="onedrive-root"
FOLDER_MIME="application/vnd.microsoft.folder"
def _encode(value:str)->str:return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()
def _decode(value:str)->str:
    if not value:raise ValueError("Invalid OneDrive item id")
    try:result=base64.urlsafe_b64decode(value+"="*(-len(value)%4)).decode()
    except Exception as exc:raise ValueError("Invalid OneDrive item id") from exc
    if not result:raise ValueError("Invalid OneDrive item id")
    return result
def make_item_id(drive_id:str,item_id:str)->str:
    if not drive_id or not item_id:raise ValueError("OneDrive drive and item IDs are required")
    return f"od:item:{_encode(drive_id)}:{_encode(item_id)}"
def parse_item_id(value:str)->tuple[str,str]:
    parts=value.split(":")
    if len(parts)!=4 or parts[:2]!=["od","item"]:raise ValueError("Invalid OneDrive item id")
    return _decode(parts[2]),_decode(parts[3])
def _kind(mime:str,folder:bool)->str:
    if folder:return "folder"
    if mime.startswith("image/"):return "image"
    if mime.startswith("video/"):return "video"
    if mime=="application/pdf":return "pdf"
    if any(word in mime for word in ("document","presentation","spreadsheet","word","excel","powerpoint")):return "document"
    return "other"
def root_node(name:str="OneDrive")->AssetNode:return AssetNode(provider="onedrive",id=ONEDRIVE_ROOT_ID,name=name,kind="folder",mime_type=FOLDER_MIME,has_children=True)
def map_item(item:dict,drive_id:str,parent_node_id:str|None=None)->AssetNode:
    folder="folder" in item;mime=FOLDER_MIME if folder else (item.get("file") or {}).get("mimeType","application/octet-stream");parent=item.get("parentReference") or {};parent_id=parent_node_id
    if parent_id is None and parent.get("id"):parent_id=make_item_id(str(parent.get("driveId") or drive_id),str(parent["id"]))
    return AssetNode(provider="onedrive",id=make_item_id(drive_id,str(item["id"])),name=item.get("name") or "Untitled",kind=_kind(mime,folder),mime_type=mime,parent_id=parent_id,size=item.get("size"),modified_at=item.get("lastModifiedDateTime"),web_url=item.get("webUrl"),has_children=folder and int((item.get("folder") or {}).get("childCount") or 0)>0)
