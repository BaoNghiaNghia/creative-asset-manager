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
    image_metadata = item.get("imageMediaMetadata") or {}
    video_metadata = item.get("videoMediaMetadata") or {}
    if not isinstance(image_metadata, dict): image_metadata = {}
    if not isinstance(video_metadata, dict): video_metadata = {}
    width = image_metadata.get("width") or video_metadata.get("width")
    height = image_metadata.get("height") or video_metadata.get("height")
    try:
        image_width = int(width) if width is not None else None
    except (TypeError, ValueError):
        image_width = None
    try:
        image_height = int(height) if height is not None else None
    except (TypeError, ValueError):
        image_height = None
    try:
        duration = int(video_metadata.get("durationMillis")) if video_metadata.get("durationMillis") is not None else None
    except (TypeError, ValueError):
        duration = None
    return AssetNode(
        id=item["id"], name=name, kind=kind_for(mime),
        mime_type=mime, parent_id=(item.get("parents") or [None])[0],
        size=int(item["size"]) if item.get("size") else None,
        modified_at=item.get("modifiedTime"), thumbnail_url=item.get("thumbnailLink"),
        web_url=item.get("webViewLink"), has_children=mime == FOLDER_MIME,
        image_width=image_width, image_height=image_height, media_duration_ms=duration,
    )
