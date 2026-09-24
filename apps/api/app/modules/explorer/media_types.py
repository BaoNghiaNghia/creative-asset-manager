from app.modules.assets.media_types import infer_media_type


def is_previewable_media(filename: str | None, mime_type: str | None) -> bool:
    return infer_media_type(filename, mime_type).startswith(("image/", "video/"))
