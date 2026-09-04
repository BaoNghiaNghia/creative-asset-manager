from __future__ import annotations
from app.domain.providers.contracts import ExternalAssetCandidate,SourceChange,SourceChangePage
from app.providers.microsoft.onedrive import OneDriveClient, validate_graph_url
from app.providers.microsoft.onedrive_mapper import FOLDER_MIME,make_item_id
def _candidate(item,source_id,drive_id):
 file_data=item.get("file") or {};parent=item.get("parentReference") or {};hashes=file_data.get("hashes") or {};folder="folder" in item;item_drive=str(parent.get("driveId") or drive_id)
 return ExternalAssetCandidate(source_type="onedrive",source_id=source_id,external_asset_id=make_item_id(item_drive,str(item["id"])),filename=item.get("name"),mime_type=FOLDER_MIME if folder else file_data.get("mimeType"),size_bytes=item.get("size"),source_created_at=item.get("createdDateTime"),source_modified_at=item.get("lastModifiedDateTime"),provider_checksum=hashes.get("sha256Hash") or hashes.get("sha1Hash"),provider_version=item.get("eTag") or item.get("cTag"),source_metadata={"drive_id":item_drive,"item_id":item.get("id"),"parent_drive_id":parent.get("driveId"),"parent_item_id":parent.get("id"),"is_folder":folder,"web_url":item.get("webUrl"),"etag":item.get("eTag")})
async def list_onedrive_delta(access_token,input):
 drive_id=str(input.source_metadata.get("drive_id") or "")
 if not drive_id:raise ValueError("OneDrive source metadata must contain drive_id")
 url=validate_graph_url(input.cursor) if input.cursor else f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta";params=None if input.cursor else {"$top":str(input.page_size),"$select":"id,name,size,createdDateTime,lastModifiedDateTime,webUrl,parentReference,file,folder,deleted,eTag,cTag"}
 async with OneDriveClient(access_token) as client:
  data=await client._get(url,params)
 latest={str(item.get("id")):item for item in data.get("value") or [] if item.get("id")};changes=[]
 for item in latest.values():
  item_drive=str((item.get("parentReference") or {}).get("driveId") or drive_id);external_id=make_item_id(item_drive,str(item["id"]));deleted="deleted" in item
  changes.append(SourceChange(change_type="deleted" if deleted else "updated",external_asset_id=external_id,candidate=None if deleted else _candidate(item,input.source_id,drive_id)))
 next_link=data.get("@odata.nextLink");delta_link=data.get("@odata.deltaLink");cursor=validate_graph_url(next_link or delta_link) if next_link or delta_link else input.cursor
 return SourceChangePage(tuple(changes),cursor,bool(next_link))
