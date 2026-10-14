"""RRUGC private preview validators cannot cross tenants or revisions."""
from __future__ import annotations

from app.modules.realistic_review_ugc.image_cache_policy import (
    matches_etag,
    preview_cache_control,
    preview_etag,
)
from app.modules.realistic_review_ugc.router import _rrugc_conditional_preview


def test_etag_is_scoped_to_tenant_remote_file_rendition_and_revision():
    base = preview_etag("tenant-a", "file-a", 192, revision="file-a")
    assert base != preview_etag("tenant-b", "file-a", 192, revision="file-a")
    assert base != preview_etag("tenant-a", "file-b", 192, revision="file-b")
    assert base != preview_etag("tenant-a", "file-a", 512, revision="file-a")
    assert base != preview_etag("tenant-a", "file-a", 192, revision="file-old")


def test_cache_is_private_and_revalidates_even_for_versioned_media():
    assert preview_cache_control("file-a", "file-a") == "private, max-age=3600"
    assert preview_cache_control("file-old", "file-a") == "private, max-age=300"
    assert preview_cache_control(None, "file-a") == "private, max-age=300"


def test_only_exact_etag_matches():
    tag = preview_etag("tenant-a", "file-a", 192)
    assert matches_etag(tag, tag)
    assert matches_etag('"unrelated", ' + tag, tag)
    assert not matches_etag("W/" + tag, tag)
    assert not matches_etag("*", tag)


def test_authorized_preview_returns_304_for_same_revision_only():
    kwargs = dict(
        tenant_id="tenant-a", remote_file_id="file-a", size=512,
        revision="file-a", requested_version="file-a",
    )
    response, tag, control = _rrugc_conditional_preview(
        **kwargs, if_none_match=None,
    )
    assert response is None
    assert control == "private, max-age=3600"
    response, _, _ = _rrugc_conditional_preview(
        **kwargs, if_none_match=tag,
    )
    assert response is not None
    assert response.status_code == 304
    assert response.headers["vary"] == "Cookie"
    assert response.headers["etag"] == tag
    altered, _, _ = _rrugc_conditional_preview(
        **{**kwargs, "remote_file_id": "file-b"}, if_none_match=tag,
    )
    assert altered is None
