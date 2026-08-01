from app.modules.explorer.media_types import infer_media_type
from app.modules.explorer.schema import AssetNode

FOLDER_MIME = "application/vnd.google-apps.folder"

def kind_for(mime: str) -> str:
    if mime == FOLDER_MIME: return "folder"
    if mime.startswith("image/"): return "image"
    if mime.startswith("video/"): return "video"
    if mime == "application/pdf": return "pdf"
    if "google-apps" in mime or any(x in mime for x in ("document", "presentation", "spreadsheet")): return "document"
    return "other"

def map_drive_file(item: dict) -> AssetNode:
    name = item.get("name", "Untitled")
    mime = infer_media_type(name, item.get("mimeType"))
    return AssetNode(
        id=item["id"], name=name, kind=kind_for(mime),
        mime_type=mime, parent_id=(item.get("parents") or [None])[0],
        size=int(item["size"]) if item.get("size") else None,
        modified_at=item.get("modifiedTime"), thumbnail_url=item.get("thumbnailLink"),
        web_url=item.get("webViewLink"), has_children=mime == FOLDER_MIME,
    )
