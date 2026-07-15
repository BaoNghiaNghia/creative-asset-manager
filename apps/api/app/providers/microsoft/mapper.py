import base64

from app.modules.explorer.schema import AssetNode

ROOT_ID = "sharepoint-root"
SITE_MIME = "application/vnd.microsoft.sharepoint.site"
DRIVE_MIME = "application/vnd.microsoft.sharepoint.library"
FOLDER_MIME = "application/vnd.microsoft.folder"


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()


def _decode(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()


def make_id(kind: str, *parts: str) -> str:
    return "sp:" + kind + ":" + ":".join(_encode(part) for part in parts)


def parse_id(value: str) -> tuple[str, list[str]]:
    if value == ROOT_ID:
        return "root", []
    parts = value.split(":")
    if len(parts) < 3 or parts[0] != "sp":
        raise ValueError("Invalid SharePoint item id")
    return parts[1], [_decode(part) for part in parts[2:]]


def kind_for(mime: str, is_folder: bool) -> str:
    if is_folder:
        return "folder"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime == "application/pdf":
        return "pdf"
    if any(value in mime for value in ("document", "presentation", "spreadsheet", "word", "excel", "powerpoint")):
        return "document"
    return "other"


def root_node() -> AssetNode:
    return AssetNode(
        provider="sharepoint",
        id=ROOT_ID,
        name="SharePoint",
        kind="folder",
        mime_type=SITE_MIME,
        has_children=True,
    )


def map_site(site: dict) -> AssetNode:
    return AssetNode(
        provider="sharepoint",
        id=make_id("site", site["id"]),
        name=site.get("displayName") or site.get("name") or "SharePoint site",
        kind="folder",
        mime_type=SITE_MIME,
        parent_id=ROOT_ID,
        web_url=site.get("webUrl"),
        has_children=True,
    )


def map_drive(drive: dict, site_id: str, site_node_id: str) -> AssetNode:
    return AssetNode(
        provider="sharepoint",
        id=make_id("drive", site_id, drive["id"]),
        name=drive.get("name") or "Documents",
        kind="folder",
        mime_type=DRIVE_MIME,
        parent_id=site_node_id,
        web_url=drive.get("webUrl"),
        has_children=True,
    )


def map_item(item: dict, drive_id: str, parent_node_id: str | None = None) -> AssetNode:
    is_folder = "folder" in item
    mime = FOLDER_MIME if is_folder else (item.get("file") or {}).get("mimeType", "application/octet-stream")
    thumbnails = item.get("thumbnails") or []
    thumbnail = None
    if thumbnails:
        sizes = thumbnails[0]
        thumbnail = (sizes.get("large") or sizes.get("medium") or sizes.get("small") or {}).get("url")

    parent_reference = item.get("parentReference") or {}
    parent_id = parent_node_id
    if parent_id is None and parent_reference.get("id"):
        parent_id = make_id("item", drive_id, parent_reference["id"])

    return AssetNode(
        provider="sharepoint",
        id=make_id("item", drive_id, item["id"]),
        name=item.get("name") or "Untitled",
        kind=kind_for(mime, is_folder),
        mime_type=mime,
        parent_id=parent_id,
        size=item.get("size"),
        modified_at=item.get("lastModifiedDateTime"),
        thumbnail_url=thumbnail,
        web_url=item.get("webUrl"),
        has_children=is_folder and int((item.get("folder") or {}).get("childCount") or 0) > 0,
    )
