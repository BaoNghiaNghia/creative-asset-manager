from app.modules.explorer.preview import (
    PREVIEW_CACHE_VERSION,
    preview_cache_clear,
    preview_cache_get,
    preview_cache_put,
    preview_cache_total_bytes,
)


def test_preview_cache_key_includes_conversion_and_content_version():
    preview_cache_clear()
    key_a = (PREVIEW_CACHE_VERSION, "tenant-a", "source-a", "file-a", "etag-a")
    key_b = (PREVIEW_CACHE_VERSION, "tenant-a", "source-a", "file-a", "etag-b")
    key_future = ("v3", "tenant-a", "source-a", "file-a", "etag-a")
    preview_cache_put(key_a, b"first")
    assert preview_cache_get(key_a) == b"first"
    assert preview_cache_get(key_b) is None
    assert preview_cache_get(key_future) is None
    assert preview_cache_total_bytes() == len(b"first")
    preview_cache_clear()
